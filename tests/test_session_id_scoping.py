"""Conversation-scoped session-id derivation (#1808).

Before this fix, ``compute_session_id`` hashed only (model + system prompt),
so two concurrent or successive conversations sharing a model+system prompt
(two Claude Code tabs on one repo; a new session started after ending one)
collapsed onto ONE ``PrefixCacheTracker``. Interleaved ``update_from_response``
calls then overwrote each other's ``_last_original``/``_last_forwarded``
state, so the overlay's append-only check diverged at message 0 and never
replayed — busting the provider's cache for both conversations.
"""

from __future__ import annotations

from types import SimpleNamespace

from headroom.cache.prefix_tracker import SessionTrackerStore


def _req(session_header: str | None = None) -> SimpleNamespace:
    headers = {"x-headroom-session-id": session_header} if session_header else {}
    return SimpleNamespace(headers=headers)


def test_explicit_header_wins_regardless_of_hint() -> None:
    store = SessionTrackerStore()
    sid = store.compute_session_id(
        _req("explicit-id"),
        "gpt-4o",
        [{"role": "user", "content": "hi"}],
        conversation_hint="whatever",
    )
    assert sid == "explicit-id"


def test_same_model_system_distinct_conversations_get_distinct_ids_by_first_message() -> None:
    store = SessionTrackerStore()
    messages_a = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "conversation A"},
    ]
    messages_b = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "conversation B"},
    ]
    id_a = store.compute_session_id(_req(), "gpt-4o", messages_a)
    id_b = store.compute_session_id(_req(), "gpt-4o", messages_b)
    assert id_a != id_b


def test_same_model_system_distinct_conversations_get_distinct_ids_by_hint() -> None:
    store = SessionTrackerStore()
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "shared opener"}]
    id_a = store.compute_session_id(_req(), "gpt-4o", messages, conversation_hint="user-a")
    id_b = store.compute_session_id(_req(), "gpt-4o", messages, conversation_hint="user-b")
    assert id_a != id_b


def test_append_only_turns_keep_the_same_session_id() -> None:
    store = SessionTrackerStore()
    turn1 = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello there"}]
    turn2 = [
        *turn1,
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "continue"},
    ]
    id_turn1 = store.compute_session_id(_req(), "gpt-4o", turn1)
    id_turn2 = store.compute_session_id(_req(), "gpt-4o", turn2)
    assert id_turn1 == id_turn2


def test_hint_path_also_stable_across_append_only_turns() -> None:
    store = SessionTrackerStore()
    turn1 = [{"role": "user", "content": "hello"}]
    turn2 = [*turn1, {"role": "user", "content": "again"}]
    id_turn1 = store.compute_session_id(_req(), "gpt-4o", turn1, conversation_hint="user-a")
    id_turn2 = store.compute_session_id(_req(), "gpt-4o", turn2, conversation_hint="user-a")
    assert id_turn1 == id_turn2


def test_interleaved_conversations_do_not_share_tracker_state() -> None:
    """Two same-model+system conversations, interleaved turns, must not
    overwrite each other's recorded original/forwarded messages."""
    store = SessionTrackerStore()
    messages_a = [{"role": "user", "content": "conversation A opener"}]
    messages_b = [{"role": "user", "content": "conversation B opener"}]

    id_a = store.compute_session_id(_req(), "gpt-4o", messages_a)
    id_b = store.compute_session_id(_req(), "gpt-4o", messages_b)
    assert id_a != id_b

    tracker_a = store.get_or_create(id_a, "openai")
    tracker_b = store.get_or_create(id_b, "openai")
    assert tracker_a is not tracker_b

    tracker_a.update_from_response(
        cache_read_tokens=0,
        cache_write_tokens=10,
        messages=messages_a,
        original_messages=messages_a,
    )
    tracker_b.update_from_response(
        cache_read_tokens=0,
        cache_write_tokens=10,
        messages=messages_b,
        original_messages=messages_b,
    )

    assert store.get_or_create(id_a, "openai").get_last_original_messages() == messages_a
    assert store.get_or_create(id_b, "openai").get_last_original_messages() == messages_b


def test_no_user_message_and_no_hint_falls_back_to_model_system_only() -> None:
    """Degenerate corner case (no user message, no hint): still produces a
    valid, stable id rather than erroring."""
    store = SessionTrackerStore()
    messages = [{"role": "system", "content": "sys"}]
    sid_1 = store.compute_session_id(_req(), "gpt-4o", messages)
    sid_2 = store.compute_session_id(_req(), "gpt-4o", messages)
    assert isinstance(sid_1, str) and len(sid_1) == 16
    assert sid_1 == sid_2


def test_conversation_hint_capped_length_still_hashes() -> None:
    """A pathologically long client-supplied hint (opaque, untrusted) must not
    error; it's capped before hashing."""
    store = SessionTrackerStore()
    messages = [{"role": "user", "content": "hi"}]
    sid = store.compute_session_id(_req(), "gpt-4o", messages, conversation_hint="x" * 10_000)
    assert isinstance(sid, str) and len(sid) == 16
