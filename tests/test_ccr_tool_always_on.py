"""PR-B7 — `headroom_retrieve` tool always-on once a session has done CCR.

These tests pin three properties:

1. After a session has performed CCR even once, every subsequent
   request in that session injects the tool — even when the current
   request has no fresh compression markers.
2. A session that has NEVER done CCR does not get the tool injected.
3. The tool definition bytes are byte-stable across turns (snapshot
   test). Any future change to the tool schema must update the
   snapshot deliberately.

Tests target the canonical helper
`headroom.proxy.helpers.apply_session_sticky_ccr_tool` plus the
`SessionCcrTracker` semantics. The CCRToolInjector legacy path is
covered by `tests/test_ccr_tool_injection.py`.
"""

from __future__ import annotations

import pytest

from headroom.ccr.tool_injection import (
    CCR_TOOL_NAME,
    CCRToolInjector,
    create_ccr_tool_definition,
)
from headroom.proxy.helpers import (
    SessionCcrTracker,
    _reset_session_ccr_tracker_for_test,
    apply_session_sticky_ccr_tool,
    get_session_ccr_tracker,
    serialize_tool_definition_canonical,
)


@pytest.fixture(autouse=True)
def _reset_tracker():
    _reset_session_ccr_tracker_for_test()
    yield
    _reset_session_ccr_tracker_for_test()


def _has_ccr_tool(tools: list[dict] | None) -> bool:
    if not tools:
        return False
    for t in tools:
        n = t.get("name") or t.get("function", {}).get("name")
        if n == CCR_TOOL_NAME:
            return True
    return False


# ─── Sticky-on behavior ────────────────────────────────────────────────


def test_tool_registered_on_every_request_after_first_ccr():
    """Once a session has done CCR, the tool stays registered every turn."""
    session_id = "sess-abc-123"

    # Turn 1: this turn produced compressed content → first-time inject.
    tools_1, injected_1 = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id=session_id,
        request_id="req-1",
        existing_tools=None,
        has_compressed_content_this_turn=True,
    )
    assert injected_1 is True
    assert _has_ccr_tool(tools_1)

    # Turn 2: NO fresh compression this turn — but session has done CCR.
    # Tool MUST still be injected (PR-B7 sticky-on).
    tools_2, injected_2 = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id=session_id,
        request_id="req-2",
        existing_tools=None,
        has_compressed_content_this_turn=False,
    )
    assert injected_2 is True, "sticky replay must inject even with no fresh CCR"
    assert _has_ccr_tool(tools_2)

    # Turn 3: still no fresh compression — sticky-on still fires.
    tools_3, injected_3 = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id=session_id,
        request_id="req-3",
        existing_tools=None,
        has_compressed_content_this_turn=False,
    )
    assert injected_3 is True
    assert _has_ccr_tool(tools_3)


def test_tool_not_registered_if_session_never_did_ccr():
    """A session that never produced CCR markers gets no tool injection."""
    session_id = "fresh-session-no-ccr"

    tools, injected = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id=session_id,
        request_id="req-1",
        existing_tools=None,
        has_compressed_content_this_turn=False,
    )
    assert injected is False
    assert not _has_ccr_tool(tools)
    # Tracker must NOT have recorded this session.
    assert get_session_ccr_tracker().has_done_ccr("anthropic", session_id) is False

    # Do it again — same outcome, no state leakage.
    tools, injected = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id=session_id,
        request_id="req-2",
        existing_tools=None,
        has_compressed_content_this_turn=False,
    )
    assert injected is False
    assert not _has_ccr_tool(tools)


def test_independent_sessions_track_independently():
    """Two distinct session_ids do not bleed sticky state."""
    sess_a = "sess-A"
    sess_b = "sess-B"

    # A does CCR; B does not.
    apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id=sess_a,
        request_id="r1",
        existing_tools=None,
        has_compressed_content_this_turn=True,
    )

    # B's next turn (no fresh CCR) must NOT auto-inject.
    tools_b, injected_b = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id=sess_b,
        request_id="r2",
        existing_tools=None,
        has_compressed_content_this_turn=False,
    )
    assert injected_b is False
    assert not _has_ccr_tool(tools_b)

    # A's next turn (no fresh CCR) MUST auto-inject (sticky).
    tools_a, injected_a = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id=sess_a,
        request_id="r3",
        existing_tools=None,
        has_compressed_content_this_turn=False,
    )
    assert injected_a is True
    assert _has_ccr_tool(tools_a)


def test_provider_isolation():
    """Same session_id under anthropic vs openai are tracked independently."""
    session_id = "shared-session-id"

    # Anthropic does CCR.
    apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id=session_id,
        request_id="r1",
        existing_tools=None,
        has_compressed_content_this_turn=True,
    )

    # OpenAI must NOT inherit anthropic's sticky state.
    tools_o, injected_o = apply_session_sticky_ccr_tool(
        provider="openai",
        session_id=session_id,
        request_id="r2",
        existing_tools=None,
        has_compressed_content_this_turn=False,
    )
    assert injected_o is False
    assert not _has_ccr_tool(tools_o)


def test_existing_ccr_tool_in_client_list_skips_injection():
    """If client (e.g. via MCP) already provided headroom_retrieve, do not double up."""
    session_id = "sess-with-mcp"
    client_tool = {
        "name": CCR_TOOL_NAME,
        "description": "client-provided",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    }
    tools, injected = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id=session_id,
        request_id="r1",
        existing_tools=[client_tool],
        has_compressed_content_this_turn=True,
    )
    assert injected is False
    # Tool list still contains the client's version (not duplicated).
    names = [t.get("name") for t in tools]
    assert names.count(CCR_TOOL_NAME) == 1


def test_no_session_id_falls_back_to_per_turn_decision():
    """WS / pre-session paths with no session_id behave per-turn."""
    # No fresh CCR + no session_id → no inject.
    tools, injected = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id=None,
        request_id="r1",
        existing_tools=None,
        has_compressed_content_this_turn=False,
    )
    assert injected is False

    # Fresh CCR + no session_id → inject (per-turn).
    tools, injected = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id=None,
        request_id="r2",
        existing_tools=None,
        has_compressed_content_this_turn=True,
    )
    assert injected is True
    assert _has_ccr_tool(tools)


# ─── Byte-stable tool definition ───────────────────────────────────────


# Snapshot of the canonical Anthropic CCR tool definition. Any change
# here MUST be deliberate — bumping the schema mid-session busts every
# active session's prompt cache (the tool list bytes are part of the
# cache key).
_ANTHROPIC_CCR_TOOL_SNAPSHOT_BYTES = (
    b'{"name":"headroom_retrieve",'
    b'"description":"Retrieve original uncompressed content that was '
    b"compressed to save tokens. Use this when you need more data than "
    b"what's shown in compressed tool results. The hash is provided in "
    b'compression markers like [N items compressed... hash=abc123].",'
    b'"input_schema":{"type":"object",'
    b'"properties":{'
    b'"hash":{"type":"string",'
    b'"description":"Hash key from the compression marker '
    b"(e.g., 'abc123' from hash=abc123)\"}"
    b'},"required":["hash"]}}'
)

_OPENAI_CCR_TOOL_SNAPSHOT_BYTES = (
    b'{"type":"function",'
    b'"function":{"name":"headroom_retrieve",'
    b'"description":"Retrieve original uncompressed content that was '
    b"compressed to save tokens. Use this when you need more data than "
    b"what's shown in compressed tool results. The hash is provided in "
    b'compression markers like [N items compressed... hash=abc123].",'
    b'"parameters":{"type":"object",'
    b'"properties":{'
    b'"hash":{"type":"string",'
    b'"description":"Hash key from the compression marker '
    b"(e.g., 'abc123' from hash=abc123)\"}"
    b'},"required":["hash"]}}}'
)


def test_tool_definition_byte_stable():
    """Pin the canonical bytes of the Anthropic + OpenAI tool defs.

    PR-B7 acceptance criterion: tool definition bytes are byte-stable.
    Any future change to ``create_ccr_tool_definition`` must bump these
    snapshots deliberately.
    """
    anthropic_tool = create_ccr_tool_definition("anthropic")
    canonical_anthropic = serialize_tool_definition_canonical(anthropic_tool)
    assert canonical_anthropic == _ANTHROPIC_CCR_TOOL_SNAPSHOT_BYTES, (
        f"Anthropic CCR tool definition bytes changed.\n"
        f"  expected: {_ANTHROPIC_CCR_TOOL_SNAPSHOT_BYTES!r}\n"
        f"  actual:   {canonical_anthropic!r}\n"
        f"If this change is intentional, update the snapshot in this "
        f"test and ensure the prompt-cache implications are reviewed."
    )

    openai_tool = create_ccr_tool_definition("openai")
    canonical_openai = serialize_tool_definition_canonical(openai_tool)
    assert canonical_openai == _OPENAI_CCR_TOOL_SNAPSHOT_BYTES, (
        f"OpenAI CCR tool definition bytes changed.\n"
        f"  expected: {_OPENAI_CCR_TOOL_SNAPSHOT_BYTES!r}\n"
        f"  actual:   {canonical_openai!r}"
    )


def test_sticky_replay_returns_byte_equal_tool_each_turn():
    """The bytes injected on turn 2 must equal the bytes injected on turn 1."""
    session_id = "sess-byte-stable"

    tools_1, _ = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id=session_id,
        request_id="r1",
        existing_tools=None,
        has_compressed_content_this_turn=True,
    )
    tools_2, _ = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id=session_id,
        request_id="r2",
        existing_tools=None,
        has_compressed_content_this_turn=False,
    )
    # Both tools lists should contain exactly one CCR tool with the
    # same canonical bytes.
    ccr_1 = next(t for t in tools_1 if t.get("name") == CCR_TOOL_NAME)
    ccr_2 = next(t for t in tools_2 if t.get("name") == CCR_TOOL_NAME)
    assert serialize_tool_definition_canonical(ccr_1) == serialize_tool_definition_canonical(ccr_2)


# ─── SessionCcrTracker unit coverage ────────────────────────────────────


def test_session_ccr_tracker_monotonic_has_done_ccr():
    """``has_done_ccr`` is monotonic — never flips back to False."""
    tracker = SessionCcrTracker(max_sessions=10)
    assert tracker.has_done_ccr("anthropic", "s1") is False

    golden = serialize_tool_definition_canonical(create_ccr_tool_definition("anthropic"))
    tracker.record_ccr_done("anthropic", "s1", golden)
    assert tracker.has_done_ccr("anthropic", "s1") is True

    # Re-record with a different golden_bytes — original bytes win
    # (first-write wins) and flag stays True.
    new_golden = b'{"name":"different","input_schema":{}}'
    tracker.record_ccr_done("anthropic", "s1", new_golden)
    assert tracker.has_done_ccr("anthropic", "s1") is True
    assert tracker.get_golden_tool_bytes("anthropic", "s1") == golden


def test_session_ccr_tracker_lru_bound():
    """Tracker evicts oldest sessions once `max_sessions` is exceeded."""
    tracker = SessionCcrTracker(max_sessions=3)
    golden = b"{}"
    for i in range(5):
        tracker.record_ccr_done("anthropic", f"s{i}", golden)
    # Only 3 most recent should remain.
    assert tracker.active_sessions == 3
    assert tracker.has_done_ccr("anthropic", "s0") is False
    assert tracker.has_done_ccr("anthropic", "s1") is False
    assert tracker.has_done_ccr("anthropic", "s4") is True


def test_session_ccr_tracker_reset_clears_state():
    tracker = SessionCcrTracker(max_sessions=10)
    tracker.record_ccr_done("anthropic", "s1", b"{}")
    assert tracker.active_sessions == 1
    tracker.reset()
    assert tracker.active_sessions == 0
    assert tracker.has_done_ccr("anthropic", "s1") is False


# ─── Per-request injector legacy path (PR-B7 backwards compat) ─────────


def test_ccrtoolinjector_session_has_done_ccr_kwarg():
    """``CCRToolInjector.inject_tool_definition`` accepts session_has_done_ccr."""
    injector = CCRToolInjector(provider="anthropic", inject_tool=True)
    # No fresh markers, no sticky flag → no inject (legacy behaviour).
    tools, was = injector.inject_tool_definition(None)
    assert was is False
    assert tools == []

    # No fresh markers, sticky flag set → inject (PR-B7 path).
    tools, was = injector.inject_tool_definition(None, session_has_done_ccr=True)
    assert was is True
    assert _has_ccr_tool(tools)


# ─── Transcript-scan recovery (dangling tool_reference after /model switch) ──
# Covers headroom/proxy/ccr_marker_policy.transcript_references_ccr_tool plus the
# transcript_requires_tool wiring through apply_session_sticky_ccr_tool. Regression
# guard for the 400 "Tool reference 'headroom_retrieve' not found in available tools".

from headroom.proxy.helpers import (  # noqa: E402
    should_inject_ccr_tool,
    transcript_references_ccr_tool,
)


def _anthropic_tool_reference_msg(name=CCR_TOOL_NAME):
    return {"role": "user", "content": [{"type": "tool_reference", "name": name}]}


def _anthropic_tool_use_msg(name=CCR_TOOL_NAME):
    return {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "looking that up"},
            {"type": "tool_use", "id": "toolu_1", "name": name, "input": {"hash": "abc"}},
        ],
    }


def _openai_tool_call_msg(name=CCR_TOOL_NAME):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": name, "arguments": "{}"}}
        ],
    }


# --- policy: transcript_references_ccr_tool ---------------------------------


def test_transcript_ref_matches_anthropic_tool_reference():
    assert transcript_references_ccr_tool([_anthropic_tool_reference_msg()]) is True


def test_transcript_ref_matches_anthropic_tool_use():
    assert transcript_references_ccr_tool([_anthropic_tool_use_msg()]) is True


def test_transcript_ref_matches_nested_one_level():
    """tool_search_tool_result / tool_result nest the reference one level down."""
    msg = {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_9",
                "content": [{"type": "tool_reference", "name": CCR_TOOL_NAME}],
            }
        ],
    }
    assert transcript_references_ccr_tool([msg]) is True


def test_transcript_ref_ignores_mcp_prefixed_name():
    """Client-owned mcp__headroom__headroom_retrieve must NOT trigger injection."""
    ref = _anthropic_tool_reference_msg(name="mcp__headroom__headroom_retrieve")
    use = _anthropic_tool_use_msg(name="mcp__headroom__headroom_retrieve")
    assert transcript_references_ccr_tool([ref, use]) is False


def test_transcript_ref_tolerates_string_and_malformed_content():
    messages = [
        {"role": "user", "content": "plain string content"},
        {"role": "assistant", "content": [None, "loose text", {"type": "text"}]},
        "not-a-dict",
        {"no_content_key": True},
    ]
    assert transcript_references_ccr_tool(messages) is False


def test_transcript_ref_empty_and_none():
    assert transcript_references_ccr_tool(None) is False
    assert transcript_references_ccr_tool([]) is False


def test_transcript_ref_matches_openai_tool_calls():
    assert transcript_references_ccr_tool([_openai_tool_call_msg()], provider="openai") is True


def test_transcript_ref_openai_ignores_mcp_prefixed_name():
    msg = _openai_tool_call_msg(name="mcp__headroom__headroom_retrieve")
    assert transcript_references_ccr_tool([msg], provider="openai") is False


# --- policy: should_inject_ccr_tool transcript override ---------------------


def test_should_inject_transcript_override_beats_frozen_deferral():
    """Frozen prefix defers config injection; a dangling reference overrides it."""
    should_inject, is_marker_override = should_inject_ccr_tool(
        configured_inject_tool=True,
        frozen_message_count=5,
        has_compressed_content=False,
        transcript_requires_tool=True,
    )
    assert should_inject is True
    # is_marker_override stays specific to fresh #1006 markers, not this path.
    assert is_marker_override is False


def test_should_inject_no_transcript_no_markers_stays_deferred():
    should_inject, is_marker_override = should_inject_ccr_tool(
        configured_inject_tool=True,
        frozen_message_count=5,
        has_compressed_content=False,
        transcript_requires_tool=False,
    )
    assert should_inject is False
    assert is_marker_override is False


# --- integration: apply_session_sticky_ccr_tool transcript recovery ---------


def test_transcript_recovery_injects_on_fresh_tracker(caplog):
    """/model-switch shape: dangling ref, fresh tracker, no fresh markers → inject."""
    import logging

    with caplog.at_level(logging.INFO):
        tools, injected = apply_session_sticky_ccr_tool(
            provider="anthropic",
            session_id="rotated-session-key",
            request_id="rec-1",
            existing_tools=[],
            has_compressed_content_this_turn=False,
            transcript_requires_tool=True,
        )
    assert injected is True
    assert _has_ccr_tool(tools)
    assert "inject_transcript_recovery" in caplog.text


def test_transcript_recovery_records_sticky_state():
    """After recovery injects, the next turn replays without needing the flag."""
    session_id = "recovery-then-sticky"
    _tools1, injected1 = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id=session_id,
        request_id="rec-a",
        existing_tools=[],
        has_compressed_content_this_turn=False,
        transcript_requires_tool=True,
    )
    assert injected1 is True

    # Subsequent turn: no dangling ref, no fresh markers — sticky replay covers it.
    tools2, injected2 = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id=session_id,
        request_id="rec-b",
        existing_tools=[],
        has_compressed_content_this_turn=False,
        transcript_requires_tool=False,
    )
    assert injected2 is True
    assert _has_ccr_tool(tools2)


def test_transcript_recovery_no_session_id_path():
    tools, injected = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id=None,
        request_id="rec-ws",
        existing_tools=[],
        has_compressed_content_this_turn=False,
        transcript_requires_tool=True,
    )
    assert injected is True
    assert _has_ccr_tool(tools)


def test_reference_free_request_unchanged():
    """No dangling ref + fresh tracker + no markers → still no injection."""
    tools, injected = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id="fresh-no-ref",
        request_id="rec-none",
        existing_tools=[],
        has_compressed_content_this_turn=False,
        transcript_requires_tool=False,
    )
    assert injected is False
    assert not _has_ccr_tool(tools)


def test_transcript_recovery_skips_when_client_already_has_tool():
    """Client already provides the tool — no dangling ref, so do not double up."""
    client_tool = {"name": CCR_TOOL_NAME, "input_schema": {"type": "object"}}
    tools, injected = apply_session_sticky_ccr_tool(
        provider="anthropic",
        session_id="client-owned",
        request_id="rec-dup",
        existing_tools=[client_tool],
        has_compressed_content_this_turn=False,
        transcript_requires_tool=True,
    )
    assert injected is False
    assert len(tools) == 1


def test_transcript_recovery_openai_chat_shape():
    tools, injected = apply_session_sticky_ccr_tool(
        provider="openai",
        session_id="openai-rotated",
        request_id="rec-oai",
        existing_tools=[],
        has_compressed_content_this_turn=False,
        transcript_requires_tool=True,
    )
    assert injected is True
    assert _has_ccr_tool(tools)
