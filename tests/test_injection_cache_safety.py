"""Cache-safe CCR proactive-expansion and memory injection gating (#2186).

Both proactive-expansion and memory injection append to the live-zone tail
message (``_append_context_to_latest_non_frozen_user_turn`` / the OpenAI
sibling). Before this fix, that append ran unconditionally in token mode:

  (a) On a same-messages re-request, ``overlay_cached_prefix`` replays last
      turn's already-injected tail byte-identical, then the handler appends
      AGAIN -> the final segment busts and grows unboundedly turn over turn.
  (b) ``execute_expansions`` had no per-session dedup, so the same
      compressed-content hash could be re-injected across requests.
  (c) Claude Code's ``/compact`` starts a new session (new conversation-
      scoped session id, phase 2), but the process-wide ``ContextTracker``
      is workspace-scoped only, so old compressed content tracked under the
      pre-compact session could surface right back into the fresh one.

This covers all three at the unit level: the shared guard
(``injection_target_already_forwarded``), the per-session expansion dedup
tracker, and ``ContextTracker``'s session-scoped ``analyze_query``.
"""

from __future__ import annotations

from headroom.cache.prefix_tracker import PrefixCacheTracker
from headroom.ccr.context_tracker import ContextTracker, ContextTrackerConfig
from headroom.proxy.ccr_session_tracker import SessionExpansionDedupTracker
from headroom.proxy.helpers import injection_target_already_forwarded

# ─── injection_target_already_forwarded ──────────────────────────────────


def test_fresh_tail_message_allows_injection() -> None:
    """(b) A genuinely new turn's tail was never forwarded -> inject."""
    tracker = PrefixCacheTracker("anthropic")
    turn1 = [{"role": "user", "content": "hello"}]
    tracker.update_from_response(
        cache_read_tokens=0, cache_write_tokens=10, messages=turn1, original_messages=turn1
    )

    turn2 = [*turn1, {"role": "assistant", "content": "hi"}, {"role": "user", "content": "more"}]
    assert (
        injection_target_already_forwarded(
            turn2, prefix_tracker=tracker, current_original_messages=turn2
        )
        is False
    )


def test_same_messages_re_request_blocks_injection() -> None:
    """(a) Re-sending the exact same messages -> tail already forwarded, skip."""
    tracker = PrefixCacheTracker("anthropic")
    turn1 = [{"role": "user", "content": "hello"}]
    tracker.update_from_response(
        cache_read_tokens=0, cache_write_tokens=10, messages=turn1, original_messages=turn1
    )

    # Identical re-request: same messages, same originals.
    assert (
        injection_target_already_forwarded(
            turn1, prefix_tracker=tracker, current_original_messages=turn1
        )
        is True
    )


def test_diverged_history_allows_injection() -> None:
    """Client rewrote history (not append-only) -> not guaranteed replayed, inject."""
    tracker = PrefixCacheTracker("anthropic")
    turn1 = [{"role": "user", "content": "hello"}]
    tracker.update_from_response(
        cache_read_tokens=0, cache_write_tokens=10, messages=turn1, original_messages=turn1
    )

    diverged = [{"role": "user", "content": "totally different opener"}]
    assert (
        injection_target_already_forwarded(
            diverged, prefix_tracker=tracker, current_original_messages=diverged
        )
        is False
    )


def test_no_prior_turn_allows_injection() -> None:
    """Cold tracker (first-ever turn) -> nothing to have double-injected into."""
    tracker = PrefixCacheTracker("anthropic")
    turn1 = [{"role": "user", "content": "hello"}]
    assert (
        injection_target_already_forwarded(
            turn1, prefix_tracker=tracker, current_original_messages=turn1
        )
        is False
    )


# ─── SessionExpansionDedupTracker ─────────────────────────────────────────


def test_expansion_dedup_blocks_repeat_hash_same_session() -> None:
    """(c) Same hash proposed twice in one session -> injected only once."""
    dedup = SessionExpansionDedupTracker(max_sessions=10)

    first = dedup.filter_new("session-a", ["hash1", "hash2"])
    assert first == ["hash1", "hash2"]
    dedup.record_injected("session-a", first)

    second = dedup.filter_new("session-a", ["hash1", "hash2", "hash3"])
    assert second == ["hash3"]


def test_expansion_dedup_is_per_session_not_global() -> None:
    """Different sessions may legitimately need the same expansion."""
    dedup = SessionExpansionDedupTracker(max_sessions=10)
    dedup.record_injected("session-a", ["hash1"])

    assert dedup.filter_new("session-b", ["hash1"]) == ["hash1"]
    assert dedup.filter_new("session-a", ["hash1"]) == []


def test_expansion_dedup_lru_eviction() -> None:
    dedup = SessionExpansionDedupTracker(max_sessions=1)
    dedup.record_injected("session-a", ["hash1"])
    dedup.record_injected("session-b", ["hash2"])  # evicts session-a

    assert dedup.filter_new("session-a", ["hash1"]) == ["hash1"]
    assert dedup.filter_new("session-b", ["hash2"]) == []


# ─── ContextTracker session-scoped analyze_query (#2186 /compact repro) ──


def test_compact_new_session_does_not_see_old_sessions_expansion() -> None:
    """(d) A fresh session (post-/compact) must not get old content surfaced
    back into it, even though the workspace is unchanged."""
    tracker = ContextTracker(ContextTrackerConfig(enabled=True, proactive_expansion=True))
    tracker.track_compression(
        hash_key="abc123",
        turn_number=1,
        tool_name="Bash",
        original_count=100,
        compressed_count=10,
        workspace_key="ws-repo",
        session_id="session-before-compact",
        query_context="find auth files",
        sample_content="auth_middleware.py handles authentication",
    )

    # New session, same workspace, a query that would otherwise match.
    recs = tracker.analyze_query(
        "what about the authentication middleware?",
        current_turn=1,
        workspace_key="ws-repo",
        session_id="session-after-compact",
    )
    assert recs == []

    # The ORIGINAL session still sees its own tracked content.
    recs_same_session = tracker.analyze_query(
        "what about the authentication middleware?",
        current_turn=1,
        workspace_key="ws-repo",
        session_id="session-before-compact",
    )
    assert len(recs_same_session) == 1
    assert recs_same_session[0].hash_key == "abc123"


def test_analyze_query_without_session_id_keeps_legacy_workspace_only_scoping() -> None:
    """Backward compatibility: omitting session_id on both sides (legacy
    callers, existing tests) preserves workspace-only scoping."""
    tracker = ContextTracker(ContextTrackerConfig(enabled=True, proactive_expansion=True))
    tracker.track_compression(
        hash_key="abc123",
        turn_number=1,
        tool_name="Bash",
        original_count=100,
        compressed_count=10,
        workspace_key="ws-repo",
        query_context="find auth files",
        sample_content="auth_middleware.py handles authentication",
    )

    recs = tracker.analyze_query(
        "what about the authentication middleware?",
        current_turn=1,
        workspace_key="ws-repo",
    )
    assert len(recs) == 1
