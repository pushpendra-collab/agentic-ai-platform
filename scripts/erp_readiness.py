"""ERP integration readiness: the Phase 14 input gate and the live safety check (ADR-063).

    python -m scripts.erp_readiness                                  # no contract: BLOCKED
    python -m scripts.erp_readiness --contract erp_contract.json     # offline gate
    python -m scripts.erp_readiness --contract erp_contract.json --live   # + live checks
    python -m scripts.erp_readiness ... --json

Offline (no connection): loads and validates the ERP contract and reports the state:

* `BLOCKED`: Q1 items needed to even connect are missing (owner confirmation, database,
  role, credential delivery, confirmation that it is the intended ERP, tenant mechanism),
  or the contract is invalid. Nothing may connect. Exit 3.
* `READY_FOR_DISCOVERY`: access items are present but the contract is not VERIFIED.
  `--live` may run the read-only security inspection and the catalog check; no business
  query. Exit 4.
* `READY_FOR_ACTIVATION`: the contract is VERIFIED. `--live` runs the full gate. Exit 0
  when the live checks pass.

`--live` needs the read-only credential in `ERP_INSPECT_DATABASE_URL`, loaded from the
secret store for this command only (never a file in the repository). A multi-database
contract (`databases`, e.g. InvenLogic) needs `ERP_INSPECT_DATABASE_URLS` instead: a JSON
object of contract database key → URL; every step below then runs for every database.
In order, and stopping at the first failure:

1. refuse a URL that names the Agent DB (`DATABASE_URL` / `DATABASE_MIGRATION_URL`);
2. the Phase 9 inspector (`scripts/inspect_erp_security.py`): identity, role attributes,
   memberships, ownership, write and DDL privileges (writes probed with EXPLAIN only),
   RLS facts. Any FAIL stops here (exit 1);
3. the adapter's own gate (`ReadOnlyErpDatabase.verify` with the contract): effective
   privileges, PostgreSQL version, mapped relations/columns/types, tenant and warehouse
   isolation. Any problem stops here (exit 1);
4. smoke test: the gate's reads are the smoke test. It reads the catalog only; no
   business row is read, and the output holds check names, codes and counts only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import asyncpg  # type: ignore[import-untyped]  # no stubs (as in the inspector)

from agentic_platform.business.adapters.postgres import ReadOnlyErpDatabase
from agentic_platform.business.erp_contract import (
    ContractError,
    ErpContract,
    Verification,
    load_contract,
    missing_inputs,
)
from agentic_platform.business.ports import ErpError
from agentic_platform.core.config import ErpSettings
from agentic_platform.core.errors import AgentPlatformError
from scripts.inspect_erp_security import (
    URL_ENV,
    InspectionRefused,
    evaluate,
    inspect,
    refuse_agent_database_url,
)

# Without these Q1 items nothing may connect at all.
ACCESS_ITEMS = frozenset({"A", "B", "C", "E", "F", "G", "H"})

EXIT_READY, EXIT_FAILED, EXIT_REFUSED, EXIT_BLOCKED, EXIT_DISCOVERY = 0, 1, 2, 3, 4


@dataclass
class Readiness:
    state: str
    missing: list[dict[str, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    live: list[dict[str, str]] = field(default_factory=list)


def assess(contract: ErpContract | None, error: str | None = None) -> Readiness:
    """The offline gate (pure)."""
    missing = [m.model_dump() for m in missing_inputs(contract)]
    if error is not None:
        return Readiness("BLOCKED", missing, [error])
    if ACCESS_ITEMS & {m["item"] for m in missing}:
        return Readiness("BLOCKED", missing)
    if contract is None or contract.status is not Verification.VERIFIED:
        return Readiness("READY_FOR_DISCOVERY", missing)
    return Readiness("READY_FOR_ACTIVATION", missing)


URLS_ENV = "ERP_INSPECT_DATABASE_URLS"


async def _adapter_gate(
    url: str,
    contract: ErpContract,
    discovery: bool,
    environ: dict[str, str],
    key: str | None = None,
) -> list[str]:
    identity = contract.database_identity(key)
    assert identity is not None
    settings = ErpSettings(
        adapter="postgres",
        database_url=url,
        expected_database=identity.name,
        # The contract states the required TLS mode; an explicit ERP_SSL_MODE for this run
        # (for example a local throw-away test server) takes precedence.
        ssl_mode=environ.get("ERP_SSL_MODE")
        or (contract.access.tls_mode if contract.access else None),
    )
    database = ReadOnlyErpDatabase(
        settings,
        agent_database_urls=[
            environ.get(k, "") for k in ("DATABASE_URL", "DATABASE_MIGRATION_URL")
        ],
        # Discovery checks privileges and identity only; the schema check needs mappings.
        contract=None if discovery else contract,
        database_key=None if discovery else key,
    )
    try:
        await database.verify()
        return []
    except ErpError as exc:
        return [str(exc)]
    finally:
        await database.dispose()


def _targets(
    readiness: Readiness, contract: ErpContract, environ: dict[str, str]
) -> list[tuple[str | None, str, str]] | None:
    """(contract database key, URL, expected database name) per database to check."""
    if not contract.databases:
        url = environ.get(URL_ENV, "").strip()
        if not url:
            readiness.problems.append(f"{URL_ENV} is not set (load it from the secret store)")
            return None
        assert contract.database is not None
        return [(None, url, contract.database.name)]
    try:
        urls = json.loads(environ.get(URLS_ENV, "") or "null")
    except json.JSONDecodeError:
        urls = None  # never echo the value: it holds credentials
    if not isinstance(urls, dict) or set(urls) != set(contract.databases):
        readiness.problems.append(
            f"{URLS_ENV} must be a JSON object naming exactly the contract's databases "
            f"({', '.join(sorted(contract.databases))}); load it from the secret store"
        )
        return None
    return [(key, str(urls[key]), contract.databases[key].name) for key in sorted(urls)]


def live_checks(
    readiness: Readiness, contract: ErpContract, environ: dict[str, str]
) -> tuple[Readiness, int]:
    targets = _targets(readiness, contract, environ)
    if targets is None:
        return readiness, EXIT_REFUSED
    discovery = readiness.state == "READY_FOR_DISCOVERY"
    for key, url, name in targets:
        where = f"{key}: " if key else ""
        try:
            refuse_agent_database_url(url, environ)
            facts = asyncio.run(inspect(url, name))
        except InspectionRefused as exc:
            readiness.problems.append(f"{where}refused: {exc}")
            return readiness, EXIT_REFUSED
        except (OSError, TimeoutError, asyncpg.PostgresError) as exc:
            readiness.problems.append(f"{where}not reachable: {type(exc).__name__}")
            return readiness, EXIT_REFUSED
        findings = evaluate(facts)
        readiness.live += [
            {"check": f"{where}{f.check}", "status": f.status}
            for f in findings
            if f.status != "INFO"
        ]
        if any(f.status == "FAIL" for f in findings):
            readiness.problems.append(f"{where}security inspection failed: stopped before any read")
            return readiness, EXIT_FAILED
        try:
            problems = asyncio.run(_adapter_gate(url, contract, discovery, environ, key))
        except (AgentPlatformError, OSError, TimeoutError) as exc:
            problems = [f"adapter gate not run: {type(exc).__name__}"]
        readiness.live.append(
            {"check": f"{where}adapter.gate", "status": "FAIL" if problems else "PASS"}
        )
        readiness.problems += [f"{where}{problem}" for problem in problems]
        if problems:
            return readiness, EXIT_FAILED
    return readiness, EXIT_DISCOVERY if discovery else EXIT_READY


def main(argv: Sequence[str] | None = None, environ: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.erp_readiness")
    parser.add_argument("--contract", help="the ERP access contract (JSON, no secrets)")
    parser.add_argument("--live", action="store_true", help="also run the live safety checks")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    env = dict(os.environ) if environ is None else environ

    contract: ErpContract | None = None
    error: str | None = None
    if args.contract:
        try:
            contract = load_contract(args.contract)
        except ContractError as exc:
            error = str(exc)
    readiness = assess(contract, error)
    code = {"BLOCKED": EXIT_BLOCKED, "READY_FOR_DISCOVERY": EXIT_DISCOVERY}.get(
        readiness.state, EXIT_READY
    )
    if args.live:
        if readiness.state == "BLOCKED" or contract is None:
            readiness.problems.append("live checks refused: the input gate is BLOCKED")
        else:
            readiness, code = live_checks(readiness, contract, env)
    _print(readiness, args.json)
    return code


def _print(readiness: Readiness, as_json: bool) -> None:
    if as_json:
        payload: dict[str, Any] = {
            "state": readiness.state,
            "missing": readiness.missing,
            "problems": readiness.problems,
            "live": readiness.live,
        }
        print(json.dumps(payload, indent=2))
        return
    print(f"ERP READINESS: {readiness.state}")
    if readiness.missing:
        print("Missing Q1 inputs:")
        for item in readiness.missing:
            print(f"  {item['item']}  {item['description']}")
    for problem in readiness.problems:
        print(f"PROBLEM  {problem}")
    for check in readiness.live:
        print(f"{check['status']:<5} {check['check']}")


if __name__ == "__main__":
    sys.exit(main())
