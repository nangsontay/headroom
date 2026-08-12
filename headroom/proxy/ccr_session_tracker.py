"""Session-scoped state for sticky CCR retrieval tool injection."""

from __future__ import annotations

import threading
from collections import OrderedDict


class SessionCcrTracker:
    """Bounded LRU tracker recording per-provider/session CCR state."""

    def __init__(self, max_sessions: int) -> None:
        if max_sessions <= 0:
            raise ValueError("max_sessions must be > 0")
        self._max_sessions = max_sessions
        self._lock = threading.RLock()
        self._sessions: OrderedDict[tuple[str, str], tuple[bool, bytes | None]] = OrderedDict()

    @property
    def active_sessions(self) -> int:
        with self._lock:
            return len(self._sessions)

    def _key(self, provider: str, session_id: str) -> tuple[str, str]:
        return (provider, session_id)

    def has_done_ccr(self, provider: str, session_id: str) -> bool:
        """Return True when this session has previously performed CCR."""

        if not provider:
            raise ValueError("provider must be non-empty")
        if not session_id:
            raise ValueError("session_id must be non-empty")
        key = self._key(provider, session_id)
        with self._lock:
            entry = self._sessions.get(key)
            if entry is None:
                return False
            self._sessions.move_to_end(key)
            return entry[0]

    def get_golden_tool_bytes(self, provider: str, session_id: str) -> bytes | None:
        """Return recorded golden CCR tool-definition bytes, if any."""

        if not provider:
            raise ValueError("provider must be non-empty")
        if not session_id:
            raise ValueError("session_id must be non-empty")
        key = self._key(provider, session_id)
        with self._lock:
            entry = self._sessions.get(key)
            if entry is None:
                return None
            self._sessions.move_to_end(key)
            return entry[1]

    def record_ccr_done(
        self,
        provider: str,
        session_id: str,
        golden_tool_bytes: bytes,
    ) -> None:
        """Mark the session as having performed CCR and pin golden tool bytes."""

        if not provider:
            raise ValueError("provider must be non-empty")
        if not session_id:
            raise ValueError("session_id must be non-empty")
        if not golden_tool_bytes:
            raise ValueError("golden_tool_bytes must be non-empty")
        key = self._key(provider, session_id)
        with self._lock:
            existing = self._sessions.get(key)
            if existing is None:
                self._sessions[key] = (True, golden_tool_bytes)
            else:
                pinned = existing[1] if existing[1] is not None else golden_tool_bytes
                self._sessions[key] = (True, pinned)
            self._sessions.move_to_end(key)
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)

    def reset(self) -> None:
        """Clear all session state."""

        with self._lock:
            self._sessions.clear()


class SessionExpansionDedupTracker:
    """Bounded LRU tracker for per-session CCR proactive-expansion dedup.

    Prevents the same compressed-content hash from being proactively
    re-injected more than once per session (#2186): a same-messages
    re-request or a continued conversation must not receive duplicate
    expansion blocks for content the agent already saw this session.
    Dedup is per-session, not global — a different conversation may
    legitimately need the same expansion.
    """

    def __init__(self, max_sessions: int) -> None:
        if max_sessions <= 0:
            raise ValueError("max_sessions must be > 0")
        self._max_sessions = max_sessions
        self._lock = threading.RLock()
        self._sessions: OrderedDict[str, set[str]] = OrderedDict()

    def filter_new(self, session_id: str, hash_keys: list[str]) -> list[str]:
        """Return the subset of hash_keys not yet injected for this session.

        Counts as a use: an active session that keeps being *asked* about
        without recording anything new must not age out behind other
        sessions, or its already-seen hashes come back and get proactively
        expanded a second time.
        """

        if not session_id:
            raise ValueError("session_id must be non-empty")
        with self._lock:
            seen = self._sessions.get(session_id)
            if seen is None:
                return list(hash_keys)
            self._sessions.move_to_end(session_id)
            return [h for h in hash_keys if h not in seen]

    def record_injected(self, session_id: str, hash_keys: list[str]) -> None:
        """Mark hash_keys as injected for this session."""

        if not session_id:
            raise ValueError("session_id must be non-empty")
        if not hash_keys:
            return
        with self._lock:
            seen = self._sessions.get(session_id)
            if seen is None:
                seen = set()
                self._sessions[session_id] = seen
            seen.update(hash_keys)
            self._sessions.move_to_end(session_id)
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)

    def reset(self) -> None:
        """Clear all session state."""

        with self._lock:
            self._sessions.clear()
