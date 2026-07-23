"""CCR marker freshness and retrieval-tool injection policy."""

from __future__ import annotations

from typing import Any, Literal


def has_new_ccr_markers(
    *,
    current_detected_hashes: list[str],
    previous_forwarded_messages: list[dict[str, Any]] | None,
    provider: Literal["anthropic", "openai", "google"],
) -> bool:
    """Return whether current CCR hashes contain hashes not previously forwarded."""

    current = set(current_detected_hashes)
    if not current:
        return False
    if not previous_forwarded_messages:
        return True

    from headroom.ccr.tool_injection import CCRToolInjector

    previous = CCRToolInjector(
        provider=provider,
        inject_tool=False,
        inject_system_instructions=False,
    )
    previous.scan_for_markers(previous_forwarded_messages)
    return bool(current - set(previous.detected_hashes))


def should_inject_ccr_tool(
    *,
    configured_inject_tool: bool,
    frozen_message_count: int,
    has_compressed_content: bool,
    transcript_requires_tool: bool = False,
) -> tuple[bool, bool]:
    """Decide whether the CCR retrieval tool must be injected this turn.

    ``transcript_requires_tool`` forces injection when the about-to-forward
    transcript already names ``headroom_retrieve`` but tracker state was lost
    (a ``/model`` switch or proxy restart) — the dangling reference would
    otherwise 400. It does NOT set ``is_marker_override``: that flag stays
    specific to fresh #1006 markers so the caller can log each cause distinctly.
    """

    inject_tool = configured_inject_tool
    if inject_tool and frozen_message_count > 0:
        inject_tool = False
    is_marker_override = not inject_tool and has_compressed_content
    should_inject = inject_tool or is_marker_override or transcript_requires_tool
    return should_inject, is_marker_override


def transcript_references_ccr_tool(
    messages: list[dict[str, Any]] | None,
    *,
    tool_name: str | None = None,
    provider: Literal["anthropic", "openai", "google"] = "anthropic",
) -> bool:
    """Whether the about-to-forward transcript already names the CCR retrieve tool.

    Once an Anthropic ``tool_reference``/``tool_use`` block, or an assistant
    ``tool_calls`` entry (OpenAI chat), names ``headroom_retrieve``, the request's
    ``tools`` array MUST still carry that tool or Anthropic 400s ("Tool reference
    'headroom_retrieve' not found in available tools"). The sticky guarantee is
    model-scoped and in-memory, so a ``/model`` switch or proxy restart loses it
    while the client transcript keeps re-sending the reference. This scan lets
    injection self-heal from the transcript, per request, independent of tracker
    state.

    ``provider="google"`` is accepted for signature parity with the sibling
    policy fns but currently falls through the Anthropic matcher — Gemini's
    ``functionCall`` parts are not matched (no google CCR handler calls this yet).

    Only the exact bare ``headroom_retrieve`` name matches — a client-owned
    ``mcp__headroom__headroom_retrieve`` (registered via MCP, lifecycle owned by
    the client) must not trigger proxy injection. Tolerates string content and
    malformed blocks.
    """
    if not messages:
        return False
    if tool_name is None:
        from headroom.ccr.tool_injection import CCR_TOOL_NAME

        tool_name = CCR_TOOL_NAME

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if provider == "openai":
            if _openai_message_references_tool(msg, tool_name):
                return True
        elif _anthropic_content_references_tool(msg.get("content"), tool_name):
            return True
    return False


def _anthropic_content_references_tool(content: Any, tool_name: str) -> bool:
    """Match a bare ``tool_name`` in tool_reference/tool_use blocks (one level deep)."""
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in ("tool_reference", "tool_use") and block.get("name") == tool_name:
            return True
        # tool_search_tool_result / tool_result nest blocks one level down.
        nested = block.get("content")
        if isinstance(nested, list):
            for inner in nested:
                if (
                    isinstance(inner, dict)
                    and inner.get("type") in ("tool_reference", "tool_use")
                    and inner.get("name") == tool_name
                ):
                    return True
    return False


def _openai_message_references_tool(msg: dict[str, Any], tool_name: str) -> bool:
    """Match a bare ``tool_name`` in an assistant message's ``tool_calls`` (chat shape)."""
    tool_calls = msg.get("tool_calls")
    if not isinstance(tool_calls, list):
        return False
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function")
        name = fn.get("name") if isinstance(fn, dict) else call.get("name")
        if name == tool_name:
            return True
    return False
