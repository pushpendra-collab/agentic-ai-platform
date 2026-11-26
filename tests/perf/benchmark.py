"""Local engineering baseline for the agent turn (Phase 13). NOT a capacity claim.

    python -m tests.perf.benchmark                 # table
    python -m tests.perf.benchmark --json          # machine-readable
    python -m tests.perf.benchmark --iterations 300

Scripted model (no network, ~0 ms per call), in-memory checkpoints, logging at WARNING.
The ERP is the real read path (`PostgresErpAdapter`, InvenLogic readers) on the local
InvenLogic TEST replica in Docker (tests/erp_db.py; TEST_ERP_ADMIN_URL and
TEST_ERP_READER_URL must be set). The fake in-process ERP was removed on 2026-10-30, so
the business figures now include real PostgreSQL round trips to a local container (new
connection per read: the tests use NullPool) and are not comparable with earlier
fake-ERP baselines. What is measured otherwise is the platform's own overhead: graph
steps, Supervisor, tool registry, business service, grounding, checkpoint writes and
observability. A real model call (seconds) is not included and would dominate a
production turn.

Agent memory is switched off for the turn scenarios: the in-memory episodic adapter
(development/tests only; production uses pgvector) recalls by a linear scan over every
stored episode, so with memory on each turn is slower than the last. That growth is
reported separately (`memory_recall_growth`) instead of distorting every figure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import statistics
import time
from collections.abc import Callable
from typing import Any

from agentic_platform.agents import Agent
from agentic_platform.agents.supervisor import Supervisor
from agentic_platform.observability import capture_events
from agentic_platform.observability.events import is_stage_end
from agentic_platform.observability.metrics import percentile
from tests.business_helpers import make_service
from tests.conftest import understanding
from tests.evaluation.harness import WHO, build_agent, plan

OPEN_INVOICES = ("business.customer_open_invoices", {"customer_name": "Rahul Gupta"})
REPLY = "Rahul Gupta ke 2 open invoices hain, total ₹45,000 (TEST data)."
Turn = tuple[str, str, bool]  # (text, thread id, measured)
FOLLOW_UP = plan(
    "overdue",
    query={"capability": "customer_open_invoices", "follow_up": True,
           "customer_reference": {"ref": "ref::rahul"},  # resolved by build_agent
           "requested_metric": "overdue_amount"},
)  # fmt: skip


def _stats(samples: list[float]) -> dict[str, Any]:
    ordered = sorted(samples)
    return {
        "n": len(samples),
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": percentile(ordered, 95),
        "min_ms": round(ordered[0], 3),
        "max_ms": round(ordered[-1], 3),
    }


def _turns(make: Callable[[int], tuple[Agent, list[Turn]]], n: int,
           warmup: int) -> dict[str, Any]:  # fmt: skip
    """Run `n` measured turns (+warm-up); also returns graph overhead from events."""
    agent, turns = make(n + warmup)
    who = WHO()
    totals: list[float] = []
    overhead: list[float] = []
    for i, (text, thread, measured) in enumerate(turns):
        with capture_events() as captured:
            started = time.perf_counter()
            agent.run(text, principal=who, thread_id=thread)
            elapsed = (time.perf_counter() - started) * 1000
        if i < warmup or not measured:
            continue
        totals.append(elapsed)
        nodes = sum(e.duration_ms or 0 for e in captured.events if is_stage_end(e.name, e.stage))
        overhead.append(max(elapsed - nodes, 0.0))
    result = _stats(totals)
    result["graph_overhead_median_ms"] = round(statistics.median(overhead), 3)
    return result


def general(n: int, *, memory: bool = False) -> tuple[Agent, list[Turn]]:
    script = [x for _ in range(n) for x in (understanding(domain="general"), "An answer.")]
    agent, _ = build_agent(*script, memory=memory)
    return agent, [("What is LangGraph?", f"g{i}", True) for i in range(n)]


def general_no_checkpointer(n: int) -> tuple[Agent, list[Turn]]:
    agent, _ = general(n)
    stateless = Agent(agent.dependencies, checkpointer=None)
    return stateless, [("What is LangGraph?", f"s{i}", True) for i in range(n)]


def business(n: int) -> tuple[Agent, list[Turn]]:
    script = [x for _ in range(n) for x in (plan("pending_bill", [OPEN_INVOICES]), REPLY)]
    agent, _ = build_agent(*script, memory=False)
    return agent, [("Rahul Gupta ka pending bill batao.", f"b{i}", True) for i in range(n)]


def follow_up(n: int) -> tuple[Agent, list[Turn]]:
    script = [x for _ in range(n) for x in (plan("pending_bill", [OPEN_INVOICES]), REPLY,
                                            FOLLOW_UP, "Overdue ₹30,000 hai.")]  # fmt: skip
    agent, _ = build_agent(*script, memory=False)
    turns: list[Turn] = []
    for i in range(n):
        # The first turn of each pair primes the business focus and is not measured.
        turns += [("Rahul Gupta ka pending bill batao.", f"f{i}", False),
                  ("Uska overdue kitna hai?", f"f{i}", True)]  # fmt: skip
    return agent, turns


def unsupported(n: int) -> tuple[Agent, list[Turn]]:
    script = [plan("low_stock_and_po", [("business.low_stock_products", {})], domain="purchase")
              for _ in range(n)]  # fmt: skip
    agent, _ = build_agent(*script, memory=False)
    return agent, [("Stock kam hai, PO bana do.", f"u{i}", True) for i in range(n)]


def supervisor_decision(n: int) -> dict[str, Any]:
    supervisor = Supervisor(lambda: ["general", "business"])
    planned = plan("pending_bill", [OPEN_INVOICES], unavailable=[("purchase", "write")])
    samples = []
    for _ in range(n):
        started = time.perf_counter()
        supervisor.decide(planned, previous_domain="business", decisions_so_far=0)
        samples.append((time.perf_counter() - started) * 1000)
    return _stats(samples)


def tool_call(n: int) -> dict[str, Any]:
    agent, _ = build_agent()
    tools = agent.dependencies.tools
    who = WHO()
    args = json.dumps(OPEN_INVOICES[1])

    async def scenario() -> list[float]:
        samples = []
        for _ in range(n):
            started = time.perf_counter()
            await tools.execute("business.customer_open_invoices", args,
                                allowed_domains=("business",), principal=who)  # fmt: skip
            samples.append((time.perf_counter() - started) * 1000)
        return samples

    return _stats(asyncio.run(scenario()))


def retry_overhead(n: int) -> dict[str, Any]:
    """One business read with no failure vs one transient failure + retry (backoff 0)."""
    service, adapter = make_service()
    who = WHO()
    service._retry = service._retry.__class__(
        max_attempts=2, base_delay_seconds=0.0, retry_on=service._retry.retry_on
    )

    async def one(transient: int) -> float:
        adapter.transient_failures = transient
        started = time.perf_counter()
        await service.customer_outstanding(who, customer_name="Rahul Gupta")
        return (time.perf_counter() - started) * 1000

    async def scenario() -> tuple[list[float], list[float]]:
        clean = [await one(0) for _ in range(n)]
        retried = [await one(1) for _ in range(n)]
        return clean, retried

    clean, retried = asyncio.run(scenario())
    return {"no_failure": _stats(clean), "one_retry": _stats(retried),
            "retry_overhead_median_ms": round(statistics.median(retried)
                                              - statistics.median(clean), 3)}  # fmt: skip


def observability_cost(n: int) -> dict[str, Any]:
    """Cost of one emitted event with the default sinks, and events per business turn."""
    from agentic_platform.observability import EventName, Stage, emit

    samples = []
    for _ in range(n):
        started = time.perf_counter()
        emit(EventName.TOOL_COMPLETED, stage=Stage.TOOL, tool="business.customer_open_invoices",
             status="ok", duration_ms=1.0, attributes={"outcome": "ok"})  # fmt: skip
        samples.append((time.perf_counter() - started) * 1000)
    agent, turns = business(1)
    with capture_events() as captured:
        agent.run(turns[0][0], principal=WHO(), thread_id="obs-cost")
    per_event = statistics.median(samples)
    events = len(captured.events)
    return {"emit": _stats(samples), "events_per_business_turn": events,
            "estimated_per_turn_ms": round(per_event * events, 3)}  # fmt: skip


def memory_growth(n: int) -> dict[str, Any]:
    """General turns with the in-memory episodic adapter on (pre-existing, dev/test only)."""
    agent, turns = general(n, memory=True)
    who = WHO()
    samples = []
    for text, thread, _ in turns:
        started = time.perf_counter()
        agent.run(text, principal=who, thread_id=thread)
        samples.append((time.perf_counter() - started) * 1000)
    block = max(n // 4, 1)
    return {"turns": n, "first_quarter_median_ms": round(statistics.median(samples[:block]), 3),
            "last_quarter_median_ms": round(statistics.median(samples[-block:]), 3)}  # fmt: skip


def run(n: int, warmup: int) -> dict[str, Any]:
    logging.getLogger("agentic_platform").setLevel(logging.WARNING)
    turns = {
        "general_turn": _turns(general, n, warmup),
        "general_turn_no_checkpointer": _turns(general_no_checkpointer, n, warmup),
        "business_turn": _turns(business, n, warmup),
        "business_follow_up": _turns(follow_up, n, warmup),
        "multi_domain_unsupported": _turns(unsupported, n, warmup),
    }
    checkpoint = round(turns["general_turn"]["median_ms"]
                       - turns["general_turn_no_checkpointer"]["median_ms"], 3)  # fmt: skip
    return {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpus": os.cpu_count(),
            "processor": platform.processor(),
            "iterations": n,
            "warmup": warmup,
            "llm": "scripted (no network)",
            "erp": "PostgresErpAdapter on the InvenLogic TEST replica (Docker, NullPool)",
            "checkpointer": "InMemorySaver with the allow-list serializer",
        },
        "turns": turns,
        "checkpoint_overhead_median_ms": checkpoint,
        "supervisor_decision": supervisor_decision(n * 10),
        "erp_tool_call": tool_call(n),
        "retry": retry_overhead(n),
        "observability": observability_cost(n * 10),
        "memory_recall_growth": memory_growth(n),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    wall = time.strftime("%Y-%m-%d %H:%M:%S")
    result = run(args.iterations, args.warmup)
    result["environment"]["started"] = wall
    result["environment"]["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"{'scenario':<34}{'median ms':>10}{'p95 ms':>10}{'graph ovh':>11}")
    for name, stats in result["turns"].items():
        print(f"{name:<34}{stats['median_ms']:>10}{stats['p95_ms']:>10}"
              f"{stats['graph_overhead_median_ms']:>11}")  # fmt: skip
    for name in ("supervisor_decision", "erp_tool_call"):
        stats = result[name]
        print(f"{name:<34}{stats['median_ms']:>10}{stats['p95_ms']:>10}")
    print(f"checkpoint overhead (median)      {result['checkpoint_overhead_median_ms']} ms")
    print(f"retry overhead (median, backoff 0) {result['retry']['retry_overhead_median_ms']} ms")
    obs = result["observability"]
    print(
        f"observability: {obs['emit']['median_ms']} ms/event x "
        f"{obs['events_per_business_turn']} events = ~{obs['estimated_per_turn_ms']} ms/turn"
    )
    print(f"memory recall growth (in-memory adapter): {result['memory_recall_growth']}")
    print(json.dumps(result["environment"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
