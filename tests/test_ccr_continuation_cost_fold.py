"""CCR reactive-continuation overhead is folded into the billed cost totals,
cache-split per provider so cached tokens price at the right rate.

Regression coverage for the gap where `handle_response` returns only the final
continuation round, so the dropped rounds' real billed usage sat in /stats
`ccr_overhead` but was excluded from `cost_with_headroom` (the cost.py
"ATTRIBUTION ONLY" comment's premise that it was "already inside
cost_with_headroom" did not hold). The fix publishes the per-call dropped-round
usage, cache-split into (uncached_input, cache_read, cache_write, output), via a
ContextVar mirroring the proactive-expansion pattern; the outcome funnel folds
each bucket into the field `cost_with_headroom` prices.

All usage numbers below are SYNTHETIC fixture inputs chosen for arithmetic
clarity; assertions are on structural invariants (sums of dropped rounds,
cache-bucket placement), NOT on any production billing figure or percentage.

Context note: set/call/consume must run inside ONE asyncio.run. A ContextVar
set inside handle_response only propagates to callers in the SAME task/context
(the production flow: handle_response and emit_request_outcome run in the same
request coroutine). Note that production emit runs via asyncio.shield, which
copies context; the consume therefore clears the copy, not the parent. That is
benign while each request task emits at most one success outcome and is the same
risk class as the proactive-expansion pattern. The batch path (multiple
handle_response calls, one emit) is a known follow-up, not covered here.
"""

from __future__ import annotations

import asyncio

from headroom.ccr.response_handler import CCRResponseHandler, CCRToolResult
from headroom.ccr.tool_injection import CCR_TOOL_NAME
from headroom.proxy.outcome import (
    RequestOutcome,
    consume_pending_ccr_continuation_usage,
    emit_request_outcome,
    set_pending_ccr_continuation_usage,
)

# --- provider-shape parsing ----------------------------------------------


def test_extract_round_cost_usage_anthropic():
    f = CCRResponseHandler._extract_round_cost_usage
    got = f(
        {
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 50,
                "cache_read_input_tokens": 200,
                "cache_creation_input_tokens": 100,
            }
        },
        "anthropic",
    )
    # input_tokens is already uncached; cache fields map 1:1.
    assert got == (1000, 200, 100, 50)


def test_extract_round_cost_usage_openai_chat_splits_cached():
    f = CCRResponseHandler._extract_round_cost_usage
    got = f(
        {
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 50,
                "prompt_tokens_details": {"cached_tokens": 200},
            }
        },
        "openai",
    )
    # prompt_tokens is gross incl cached; cached lands in cache_read, not uncached.
    assert got == (800, 200, 0, 50)


def test_extract_round_cost_usage_openai_responses_shape():
    f = CCRResponseHandler._extract_round_cost_usage
    got = f(
        {
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 50,
                "input_tokens_details": {"cached_tokens": 200},
            }
        },
        "openai_responses",
    )
    assert got == (800, 200, 0, 50)


def test_extract_round_cost_usage_prefers_toplevel_cache_keys():
    f = CCRResponseHandler._extract_round_cost_usage
    got = f(
        {
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 50,
                "cache_read_input_tokens": 300,
                "prompt_tokens_details": {"cached_tokens": 999},
            }
        },
        "openai",
    )
    # Authoritative top-level Anthropic/Bedrock key wins over the details shape.
    assert got == (700, 300, 0, 50)


def test_extract_round_cost_usage_absent_usage():
    f = CCRResponseHandler._extract_round_cost_usage
    assert f({}, "anthropic") == (0, 0, 0, 0)
    assert f({"usage": None}, "openai") == (0, 0, 0, 0)
    assert f("not a dict", "anthropic") == (0, 0, 0, 0)


# --- handle_response publishes per-call dropped-round usage --------------


def _tool_use_response(rid, usage):
    return {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": f"tu_{rid}",
                "name": CCR_TOOL_NAME,
                "input": {"hash": "a" * 24},
            }
        ],
        "stop_reason": "tool_use",
        "usage": usage,
    }


def _final_response(usage):
    return {
        "role": "assistant",
        "content": [{"type": "text", "text": "final answer"}],
        "stop_reason": "end_turn",
        "usage": usage,
    }


def test_handle_response_publishes_per_call_dropped_round_usage(monkeypatch):
    """3 anthropic rounds (r1, r2 dropped; r3 final): the published 4-tuple
    equals the SUM of the two dropped rounds' cache-split usage only."""
    handler = CCRResponseHandler()
    monkeypatch.setattr(
        handler,
        "_execute_retrieval",
        lambda call: CCRToolResult(
            tool_call_id=call.tool_call_id,
            content="{}",
            success=True,
            items_retrieved=1,
            tokens_retrieved=10,
        ),
    )
    # Synthetic anthropic per-round usage (arbitrary, with cache fields).
    U1 = {
        "input_tokens": 100_000,
        "output_tokens": 500,
        "cache_read_input_tokens": 40_000,
        "cache_creation_input_tokens": 5_000,
    }
    U2 = {
        "input_tokens": 105_000,
        "output_tokens": 600,
        "cache_read_input_tokens": 42_000,
        "cache_creation_input_tokens": 6_000,
    }
    U3 = {
        "input_tokens": 110_000,
        "output_tokens": 800,
        "cache_read_input_tokens": 44_000,
        "cache_creation_input_tokens": 7_000,
    }

    call_count = [0]

    async def api_call(messages, tools):
        call_count[0] += 1
        if call_count[0] == 1:
            return _tool_use_response("r2", U2)
        return _final_response(U3)

    async def main():
        consume_pending_ccr_continuation_usage()
        final = await handler.handle_response(
            _tool_use_response("r1", U1),
            [{"role": "user", "content": "q"}],
            [],
            api_call,
            "anthropic",
        )
        return final, consume_pending_ccr_continuation_usage()

    final, published = asyncio.run(main())

    assert final["usage"]["input_tokens"] == U3["input_tokens"]  # final round returned
    assert published == (
        U1["input_tokens"] + U2["input_tokens"],
        U1["cache_read_input_tokens"] + U2["cache_read_input_tokens"],
        U1["cache_creation_input_tokens"] + U2["cache_creation_input_tokens"],
        U1["output_tokens"] + U2["output_tokens"],
    )


def test_no_continuation_publishes_nothing():
    async def main():
        consume_pending_ccr_continuation_usage()
        await CCRResponseHandler().handle_response(
            _final_response({"input_tokens": 50, "output_tokens": 20}),
            [{"role": "user", "content": "q"}],
            [],
            None,
            "anthropic",
        )
        return consume_pending_ccr_continuation_usage()

    assert asyncio.run(main()) is None


# --- outcome funnel fold -------------------------------------------------


class _Metrics:
    def __init__(self):
        self.requested = []

    async def record_failed(self, provider):
        pass

    async def record_request(self, **kwargs):
        self.requested.append(kwargs)


class _Handler:
    def __init__(self):
        self.metrics = _Metrics()


def test_emit_request_outcome_folds_continuation_usage_into_billed_tokens():
    """The funnel adds each published bucket to the matching cost field."""
    handler = _Handler()
    base_out, base_uin, base_cr, base_cw = 800, 110_000, 30_000, 3_000
    ct_uin, ct_cr, ct_cw, ct_out = 205_000, 80_000, 11_000, 1_100  # dropped rounds (synthetic)

    outcome = RequestOutcome(
        request_id="req-ccr-fold",
        provider="anthropic",
        model="m",
        status_code=200,
        original_tokens=base_uin,
        optimized_tokens=base_uin,
        output_tokens=base_out,
        tokens_saved=0,
        attempted_input_tokens=base_uin,
        uncached_input_tokens=base_uin,
        cache_read_tokens=base_cr,
        cache_write_tokens=base_cw,
    )

    async def main():
        set_pending_ccr_continuation_usage((ct_uin, ct_cr, ct_cw, ct_out))
        await emit_request_outcome(handler, outcome)
        return consume_pending_ccr_continuation_usage()

    leftover = asyncio.run(main())

    rec = handler.metrics.requested[0]
    assert rec["output_tokens"] == base_out + ct_out
    assert rec["uncached_input_tokens"] == base_uin + ct_uin
    assert rec["cache_read_tokens"] == base_cr + ct_cr
    assert rec["cache_write_tokens"] == base_cw + ct_cw
    assert leftover is None


def test_emit_request_outcome_5xx_does_not_fold_continuation():
    handler = _Handler()
    outcome = RequestOutcome(
        request_id="req-ccr-5xx",
        provider="anthropic",
        model="m",
        status_code=529,
        original_tokens=100,
        optimized_tokens=100,
        output_tokens=10,
        tokens_saved=0,
        attempted_input_tokens=100,
        uncached_input_tokens=100,
    )

    async def main():
        set_pending_ccr_continuation_usage((999_999, 999_999, 999_999, 999_999))
        await emit_request_outcome(handler, outcome)
        return consume_pending_ccr_continuation_usage()

    leftover = asyncio.run(main())
    assert handler.metrics.requested == []
    assert leftover is None


# --- end-to-end: handle_response -> emit in one task ---------------------


def test_end_to_end_handle_then_emit_folds_dropped_rounds(monkeypatch):
    """Full wiring in one task: handle_response publishes, emit consumes and
    folds, so record_request sees final-round + dropped-round tokens."""
    ccr = CCRResponseHandler()
    monkeypatch.setattr(
        ccr,
        "_execute_retrieval",
        lambda call: CCRToolResult(
            tool_call_id=call.tool_call_id,
            content="{}",
            success=True,
            items_retrieved=1,
            tokens_retrieved=10,
        ),
    )
    U1 = {
        "input_tokens": 100_000,
        "output_tokens": 500,
        "cache_read_input_tokens": 40_000,
        "cache_creation_input_tokens": 5_000,
    }
    U2 = {
        "input_tokens": 12_000,
        "output_tokens": 80,
        "cache_read_input_tokens": 1_000,
        "cache_creation_input_tokens": 0,
    }

    async def api_call(messages, tools):
        return _final_response(U2)

    async def main():
        consume_pending_ccr_continuation_usage()
        await ccr.handle_response(
            _tool_use_response("r1", U1),
            [{"role": "user", "content": "q"}],
            [],
            api_call,
            "anthropic",
        )
        # The handler would normally build the outcome from the final round.
        handler = _Handler()
        outcome = RequestOutcome(
            request_id="req-e2e",
            provider="anthropic",
            model="m",
            status_code=200,
            original_tokens=U2["input_tokens"],
            optimized_tokens=U2["input_tokens"],
            output_tokens=U2["output_tokens"],
            tokens_saved=0,
            attempted_input_tokens=U2["input_tokens"],
            uncached_input_tokens=U2["input_tokens"],
            cache_read_tokens=U2["cache_read_input_tokens"],
            cache_write_tokens=U2["cache_creation_input_tokens"],
        )
        await emit_request_outcome(handler, outcome)
        return handler.metrics.requested[0]

    rec = asyncio.run(main())
    # final (U2) + dropped (U1) per bucket.
    assert rec["uncached_input_tokens"] == U1["input_tokens"] + U2["input_tokens"]
    assert rec["cache_read_tokens"] == U1["cache_read_input_tokens"] + U2["cache_read_input_tokens"]
    assert (
        rec["cache_write_tokens"]
        == U1["cache_creation_input_tokens"] + U2["cache_creation_input_tokens"]
    )
    assert rec["output_tokens"] == U1["output_tokens"] + U2["output_tokens"]
