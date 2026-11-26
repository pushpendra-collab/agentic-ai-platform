"""Read-only load profile for the platform (Phase 8). LOCAL MEASUREMENT, not capacity.

Drives the deployed stack through the TLS gateway with real OIDC access tokens from the
reference Keycloak, and samples API container CPU/memory, PostgreSQL connections and
Redis usage while it runs. Only operations that read, plus agent turns answered by the
LLM stub (deploy/loadtest/llm_stub.py), which write only conversation state, checkpoints
and audit rows in the disposable `agentic_test` database. No business operation exists
or is exercised.

    .venv/Scripts/python deploy/loadtest/run_load.py \
        --base-url https://localhost:8443 --ca deploy/gateway/certs/tls.crt \
        --keycloak http://localhost:8080 --duration 30 --concurrency 10 \
        --out deploy/loadtest/results/<name>.json

Scenarios (each runs for --duration seconds at --concurrency workers):
    probes   GET /health, GET /ready
    reads    GET /api/v1/me, conversations list/metadata/history, memory facts, approvals
    turns    POST /api/v1/agent/turn (stub LLM; --turn-concurrency workers)
    mixed    90 % reads, 10 % turns
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import subprocess
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

REALM = "agentic-verify"
CLIENT_ID = "agentic-cli"
CLIENT_SECRET = "verify-client-secret-not-a-real-secret"  # throw-away test realm only
PASSWORD = "Verify-Password-1"  # throw-away test users only
USERS = ("asha", "ravi", "gita")


@dataclass
class Sample:
    name: str
    status: int
    seconds: float


@dataclass
class Result:
    scenario: str
    duration_s: float
    concurrency: int
    samples: list[Sample] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        def stats(items: list[Sample]) -> dict[str, Any]:
            latencies = sorted(s.seconds * 1000 for s in items)
            errors = [s for s in items if s.status >= 500 or s.status == 0]
            limited = [s for s in items if s.status == 429]

            def pct(p: float) -> float:
                if not latencies:
                    return 0.0
                return round(latencies[min(len(latencies) - 1, int(p * len(latencies)))], 1)

            return {
                "requests": len(items),
                "rps": round(len(items) / self.duration_s, 1),
                "p50_ms": pct(0.50),
                "p95_ms": pct(0.95),
                "p99_ms": pct(0.99),
                "max_ms": round(latencies[-1], 1) if latencies else 0.0,
                "mean_ms": round(statistics.fmean(latencies), 1) if latencies else 0.0,
                "error_rate": round(len(errors) / len(items), 4) if items else 0.0,
                "rate_limited": len(limited),
                "statuses": dict(Counter(str(s.status) for s in items)),
            }

        by_name: dict[str, list[Sample]] = {}
        for sample in self.samples:
            by_name.setdefault(sample.name, []).append(sample)
        return {
            "scenario": self.scenario,
            "duration_s": self.duration_s,
            "concurrency": self.concurrency,
            "total": stats(self.samples),
            "operations": {name: stats(items) for name, items in sorted(by_name.items())},
            "resources": summarize_resources(self.resources),
        }


# --- resource sampling (docker CLI; local stack only) ---------------------------------------


def _docker(*args: str) -> str:
    try:
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def sample_resources(api_container: str, redis_password: str) -> dict[str, Any]:
    stats = _docker("stats", "--no-stream", "--format", "{{json .}}", api_container)
    sample: dict[str, Any] = {"t": time.time()}
    if stats:
        data = json.loads(stats.splitlines()[0])
        sample["api_cpu_pct"] = float(data["CPUPerc"].rstrip("%") or 0)
        sample["api_mem"] = data["MemUsage"].split("/")[0].strip()
    connections = _docker(
        "exec", "agentic-postgres", "psql", "-U", "agentic_owner", "-d", "agentic", "-tAc",
        "SELECT count(*) FROM pg_stat_activity WHERE datname = 'agentic_test'",
    )  # fmt: skip
    if connections.isdigit():
        sample["pg_connections"] = int(connections)
    info = _docker(
        "exec", "agentic-redis", "redis-cli", "-a", redis_password, "--no-auth-warning",
        "INFO", "everything",
    )  # fmt: skip
    for line in info.splitlines():
        key, _, value = line.partition(":")
        if key in ("connected_clients", "instantaneous_ops_per_sec"):
            sample[f"redis_{key}"] = int(value)
        elif key == "used_memory":
            sample["redis_used_memory_mb"] = round(int(value) / 1_048_576, 2)
    return sample


def summarize_resources(samples: list[dict[str, Any]]) -> dict[str, Any]:
    def series(key: str) -> list[float]:
        return [float(s[key]) for s in samples if key in s]

    out: dict[str, Any] = {"samples": len(samples)}
    for key in ("api_cpu_pct", "pg_connections", "redis_connected_clients",
                "redis_instantaneous_ops_per_sec", "redis_used_memory_mb"):  # fmt: skip
        values = series(key)
        if values:
            out[key] = {"max": max(values), "mean": round(statistics.fmean(values), 1)}
    mems = [s["api_mem"] for s in samples if "api_mem" in s]
    if mems:
        out["api_mem_last"] = mems[-1]
    return out


class ResourceSampler(threading.Thread):
    def __init__(self, api_container: str, redis_password: str, every: float = 2.0) -> None:
        super().__init__(daemon=True)
        self.api_container, self.redis_password, self.every = api_container, redis_password, every
        self.samples: list[dict[str, Any]] = []
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            self.samples.append(sample_resources(self.api_container, self.redis_password))
            self._stop_event.wait(self.every)

    def stop(self) -> list[dict[str, Any]]:
        self._stop_event.set()
        self.join(timeout=15)
        return self.samples


# --- workload ------------------------------------------------------------------------------


def fetch_tokens(keycloak: str) -> dict[str, str]:
    tokens = {}
    for user in USERS:
        response = httpx.post(
            f"{keycloak}/realms/{REALM}/protocol/openid-connect/token",
            data={"grant_type": "password", "client_id": CLIENT_ID,
                  "client_secret": CLIENT_SECRET, "username": user, "password": PASSWORD},
            timeout=10,
        )  # fmt: skip
        response.raise_for_status()
        tokens[user] = str(response.json()["access_token"])
    return tokens


class Workload:
    def __init__(self, client: httpx.AsyncClient, tokens: dict[str, str]) -> None:
        self.client = client
        self.tokens = tokens
        self.threads: dict[str, str] = {}

    def auth(self, user: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[user]}"}

    async def seed(self) -> None:
        """One conversation per user, so reads have something to read."""
        for user in self.tokens:
            response = await self.client.post(
                "/api/v1/agent/turn", json={"message": "Hello"}, headers=self.auth(user)
            )
            if response.status_code == 200:
                self.threads[user] = response.json()["thread_id"]

    def read_operations(self, user: str) -> list[tuple[str, str]]:
        ops = [
            ("me", "/api/v1/me"),
            ("conversations.list", "/api/v1/conversations"),
            ("memory.facts", "/api/v1/memory/facts"),
            ("approvals.list", "/api/v1/approvals"),
        ]
        thread = self.threads.get(user)
        if thread:
            ops += [
                ("conversation.get", f"/api/v1/conversations/{thread}"),
                ("conversation.history", f"/api/v1/conversations/{thread}/history"),
            ]
        return ops

    async def timed(self, name: str, method: str, path: str, **kw: Any) -> Sample:
        started = time.perf_counter()
        try:
            response = await self.client.request(method, path, **kw)
            status = response.status_code
        except httpx.HTTPError:
            status = 0
        return Sample(name, status, time.perf_counter() - started)

    async def probe(self) -> Sample:
        path = random.choice(("/health", "/ready"))
        return await self.timed(path.strip("/"), "GET", path)

    async def read(self) -> Sample:
        user = random.choice(list(self.tokens))
        name, path = random.choice(self.read_operations(user))
        return await self.timed(name, "GET", path, headers=self.auth(user))

    async def turn(self) -> Sample:
        user = random.choice(list(self.tokens))
        return await self.timed(
            "agent.turn", "POST", "/api/v1/agent/turn",
            json={"message": "Hello"}, headers=self.auth(user),
        )  # fmt: skip


async def run_scenario(
    workload: Workload, scenario: str, duration: float, concurrency: int
) -> list[Sample]:
    pick = {
        "probes": workload.probe,
        "reads": workload.read,
        "turns": workload.turn,
        "mixed": lambda: workload.turn() if random.random() < 0.1 else workload.read(),
    }[scenario]
    deadline = time.perf_counter() + duration
    samples: list[Sample] = []

    async def worker() -> None:
        while time.perf_counter() < deadline:
            samples.append(await pick())

    await asyncio.gather(*(worker() for _ in range(concurrency)))
    return samples


async def main_async(args: argparse.Namespace) -> list[dict[str, Any]]:
    verify: Any = args.ca if args.ca else True
    limits = httpx.Limits(max_connections=max(args.concurrency, args.turn_concurrency) + 5)
    reports = []
    async with httpx.AsyncClient(
        base_url=args.base_url, verify=verify, timeout=args.timeout, limits=limits
    ) as client:
        for scenario in args.scenarios:
            tokens = fetch_tokens(args.keycloak)  # fresh 5-minute tokens per scenario
            workload = Workload(client, tokens)
            await workload.seed()
            concurrency = args.turn_concurrency if scenario == "turns" else args.concurrency
            sampler = ResourceSampler(args.api_container, args.redis_password)
            sampler.start()
            started = time.perf_counter()
            samples = await run_scenario(workload, scenario, args.duration, concurrency)
            elapsed = time.perf_counter() - started
            result = Result(scenario, round(elapsed, 1), concurrency, samples, sampler.stop())
            summary = result.summary()
            reports.append(summary)
            total = summary["total"]
            print(
                f"{scenario:7s} c={concurrency:<3d} n={total['requests']:<6d} "
                f"rps={total['rps']:<7} p50={total['p50_ms']}ms p95={total['p95_ms']}ms "
                f"p99={total['p99_ms']}ms err={total['error_rate']} 429={total['rate_limited']} "
                f"statuses={total['statuses']}",
                flush=True,
            )
    return reports


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://localhost:8443")
    parser.add_argument("--ca", default="deploy/gateway/certs/tls.crt")
    parser.add_argument("--keycloak", default="http://localhost:8080")
    parser.add_argument("--duration", type=float, default=30)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--turn-concurrency", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--scenarios", nargs="+", default=["probes", "reads", "turns", "mixed"])
    parser.add_argument("--api-container", default="agentic_ai01102026-api-1")
    parser.add_argument("--redis-password", default="agentic_redis_dev")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    reports = asyncio.run(main_async(args))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(reports, indent=2), encoding="utf-8")
        print(f"report: {args.out}")


if __name__ == "__main__":
    main()
