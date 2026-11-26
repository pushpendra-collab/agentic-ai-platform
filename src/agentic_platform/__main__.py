"""Command-line entry point.

    python -m agentic_platform "What time is it in Asia/Kolkata?"   # one turn
    python -m agentic_platform                                       # interactive
    python -m agentic_platform --thread <id> "And in London?"        # continue a conversation
    python -m agentic_platform --thread <id> --status                # thread state (JSON)
    python -m agentic_platform --thread <id> --resume                # crashed turn or approval
    python -m agentic_platform --thread <id> --resume "yes"          # answer an interrupt
    python -m agentic_platform --thread <id> --forget                # delete a conversation
    python -m agentic_platform --db-health                           # database health (JSON)
    python -m agentic_platform --redis-health                        # Redis health (JSON)
    python -m agentic_platform --ingest handbook.md --role member --role knowledge_manager
    python -m agentic_platform --memory-purge                        # maintenance jobs, once
    python -m agentic_platform --approvals                           # open approval requests
    python -m agentic_platform --approve <id> [--note "..."]         # or --reject <id>
    python -m agentic_platform --audit [--thread <id>]               # audit trail (audit.read)
    python -m agentic_platform --trace "Rahul ka bill?"              # + turn diagnostics (stderr)

Identity (Phase 5). Every command runs as an authenticated principal:

* `--token <token>` (or env AGENTIC_TOKEN): a signed token, verified with
  AUTH_TOKEN_SECRET. `--issue-token` mints a development token (not in production).
* otherwise the local development identity: `--tenant`, `--user` and `--role`
  (repeatable; default AUTH_LOCAL_ROLES), allowed unless APP_ENV=production or
  AUTH_ALLOW_LOCAL_IDENTITY=false.

Conversation history is kept by the checkpointer chosen by AGENT_CHECKPOINTER:
in PostgreSQL when DATABASE_URL is set (survives restarts; continue with
`--thread`), otherwise in memory for this process only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from agentic_platform.agents import Agent, AgentResponse
from agentic_platform.agents.threads import new_thread_id
from agentic_platform.application.audit import AuditReader
from agentic_platform.cache import RedisConnection
from agentic_platform.core.config import get_settings
from agentic_platform.core.errors import AgentPlatformError, FeatureUnavailableError
from agentic_platform.core.logging import configure_logging
from agentic_platform.db.engine import Database
from agentic_platform.security.identity import Principal
from agentic_platform.services.database_health import DatabaseHealthService
from agentic_platform.services.redis_health import RedisHealthService

TOKEN_ENV = "AGENTIC_TOKEN"


def _print(response: AgentResponse, as_json: bool) -> None:
    if as_json:
        print(response.model_dump_json(indent=2))
        return
    print(f"[{response.status}] {response.message}")
    for interrupt in response.interrupts:
        print(f"  waiting for: {json.dumps(interrupt.value, ensure_ascii=False)}")


def _trace(agent: Agent, response: AgentResponse, args: argparse.Namespace) -> None:
    """Phase 13: the turn's safe diagnostics (ids, statuses, codes, durations) on stderr."""
    if args.trace:
        print(agent.diagnose(response.request_id).render(), file=sys.stderr)


def _dump(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str, ensure_ascii=False))


async def _db_health() -> int:
    settings = get_settings()
    configure_logging(settings.logging)
    database = Database(settings.database) if settings.database.configured else None
    try:
        health = await DatabaseHealthService(database).check()
    finally:
        if database is not None:
            await database.dispose()
    print(health.model_dump_json(indent=2))
    return 0 if health.status == "ok" else 1


async def _redis_health() -> int:
    settings = get_settings()
    configure_logging(settings.logging)
    connection = RedisConnection(settings.redis) if settings.redis.configured else None
    try:
        health = await RedisHealthService(connection).check()
    finally:
        if connection is not None:
            await connection.dispose()
    print(health.model_dump_json(indent=2))
    return 0 if health.status == "ok" else 1


async def _authenticate(agent: Agent, args: argparse.Namespace) -> Principal:
    """The request's identity: a verified token, or the local development identity."""
    security = agent.dependencies.security
    token = args.token or os.environ.get(TOKEN_ENV)
    if token:
        if security.tokens is None:
            raise SystemExit("Token sign-in needs AUTH_TOKEN_SECRET.")
        return await security.tokens.authenticate(token)
    return security.local_identity.principal(
        tenant_id=args.tenant, user_id=args.user, roles=args.role or None
    )


async def _interactive(agent: Agent, principal: Principal, args: argparse.Namespace) -> int:
    thread_id = args.thread or new_thread_id()
    print(f"Conversation {thread_id}. Type a message, or 'exit' to quit.")
    while True:
        try:
            line = await asyncio.to_thread(input, "> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if line.strip().lower() in {"exit", "quit"}:
            return 0
        try:
            response = await agent.arun(line, principal=principal, thread_id=thread_id)
            _print(response, args.json)
            _trace(agent, response, args)
        except AgentPlatformError as exc:
            print(exc.public_message)


async def _ingest(agent: Agent, principal: Principal, path: str) -> int:
    memory = agent.dependencies.authorized_memory()
    if memory is None:
        print("Memory is disabled (MEMORY_ENABLED=false).", file=sys.stderr)
        return 2
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    name = os.path.basename(path)
    source_ref = "".join(c if c.isalnum() or c in "_.:-" else "_" for c in name)[:100]
    chunks = await memory.ingest_document(principal, source_ref=source_ref, text=text, title=path)
    print(f"Ingested {len(chunks)} chunk(s) as '{source_ref}' for tenant {principal.tenant_id}.")
    return 0


async def _maintenance(agent: Agent) -> int:
    """Retention jobs (trusted operator), one bounded run of each (Phase 6 maintenance)."""
    from agentic_platform.maintenance import build_maintenance_runner

    reports = await build_maintenance_runner(agent.dependencies).run_all()
    for report in reports:
        print(report.model_dump_json())
    return 1 if any(r.status in ("failed", "partial") for r in reports) else 0


async def _audit(agent: Agent, principal: Principal, thread_id: str | None) -> int:
    security = agent.dependencies.security
    reader = AuditReader(security.authorizer, agent.dependencies.database)
    try:
        records = await reader.list(principal, thread_id=thread_id, limit=50)
    except FeatureUnavailableError:
        print("The audit log needs DATABASE_URL.", file=sys.stderr)
        return 2
    _dump([r.model_dump(mode="json") for r in records])
    return 0


async def _dispatch(agent: Agent, args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.memory_purge:
        return await _maintenance(agent)
    if args.issue_token:
        tokens = agent.dependencies.security.tokens
        if tokens is None or settings.env == "production":
            print("--issue-token needs AUTH_TOKEN_SECRET, outside production.", file=sys.stderr)
            return 2
        print(
            tokens.issue(
                user_id=args.user or settings.memory.default_user_id or "",
                tenant_id=args.tenant or settings.memory.default_tenant_id,
                roles=args.role or settings.auth.local_roles,
                ttl_seconds=args.token_ttl,
            )
        )
        return 0

    principal = await _authenticate(agent, args)
    approvals = agent.dependencies.security.approvals
    if args.ingest:
        return await _ingest(agent, principal, args.ingest)
    if args.approvals:
        _dump([r.public_view() for r in await approvals.list_open(principal)])
        return 0
    if args.approve or args.reject:
        record = await approvals.decide(
            principal, args.approve or args.reject, approve=bool(args.approve), note=args.note
        )
        _dump(record.public_view())
        return 0
    if args.audit:
        return await _audit(agent, principal, args.thread)
    if args.status:
        status = await agent.athread_status(args.thread, principal=principal)
        print(status.model_dump_json(indent=2))
        return 0
    if args.forget:
        _dump(await agent.aforget_conversation(args.thread, principal=principal))
        return 0
    if args.resume:
        response = await agent.aresume(args.thread, principal=principal, resume_value=args.message)
    elif args.message:
        response = await agent.arun(args.message, principal=principal, thread_id=args.thread)
    else:
        return await _interactive(agent, principal, args)
    _print(response, args.json)
    _trace(agent, response, args)
    if not args.json:
        print(f"(conversation {response.thread_id})", file=sys.stderr)
    return 0 if response.status != "failed" else 1


async def _run(agent: Agent, args: argparse.Namespace) -> int:
    try:
        return await _dispatch(agent, args)
    finally:
        await agent.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentic-platform")
    parser.add_argument(
        "message", nargs="?", help="Message to send (or the answer, with --resume)."
    )
    parser.add_argument("--json", action="store_true", help="Print the full AgentResponse as JSON.")
    parser.add_argument(
        "--trace", action="store_true", help="Also print the turn's diagnostics (stderr)."
    )
    parser.add_argument("--db-health", action="store_true", help="Check the database and exit.")
    parser.add_argument("--redis-health", action="store_true", help="Check Redis and exit.")
    parser.add_argument("--thread", help="Conversation id to continue (printed by earlier runs).")
    parser.add_argument(
        "--resume", action="store_true", help="Resume the thread's unfinished turn."
    )
    parser.add_argument("--status", action="store_true", help="Show the thread's state.")
    parser.add_argument("--forget", action="store_true", help="Delete the thread (--thread).")
    identity = parser.add_argument_group("identity")
    identity.add_argument("--token", help=f"Signed identity token (or env {TOKEN_ENV}).")
    identity.add_argument(
        "--tenant", help="Local identity: tenant id. Default: MEMORY_DEFAULT_TENANT_ID."
    )
    identity.add_argument(
        "--user", help="Local identity: user id. Default: MEMORY_DEFAULT_USER_ID."
    )
    identity.add_argument(
        "--role",
        action="append",
        help="Local identity: role (repeatable). Default: AUTH_LOCAL_ROLES.",
    )
    identity.add_argument(
        "--issue-token", action="store_true", help="Print a development token and exit."
    )
    identity.add_argument("--token-ttl", type=int, default=3600, help="--issue-token lifetime (s).")
    parser.add_argument(
        "--ingest", metavar="FILE", help="Add a UTF-8 text file as tenant knowledge."
    )
    parser.add_argument(
        "--memory-purge",
        action="store_true",
        help="Maintenance: run every retention job once (expired memory, overdue "
        "approvals, expired idempotency records), then exit.",
    )
    hitl = parser.add_argument_group("approvals")
    hitl.add_argument("--approvals", action="store_true", help="List open approval requests.")
    hitl.add_argument("--approve", metavar="ID", help="Approve a request.")
    hitl.add_argument("--reject", metavar="ID", help="Reject a request.")
    hitl.add_argument("--note", help="Note recorded with --approve / --reject.")
    parser.add_argument("--audit", action="store_true", help="Show the audit trail (audit.read).")
    args = parser.parse_args(argv)

    if args.db_health:
        return asyncio.run(_db_health())
    if args.redis_health:
        return asyncio.run(_redis_health())
    if (args.resume or args.status or args.forget) and not args.thread:
        parser.error("--resume, --status and --forget need --thread")

    # Replies hold "₹"; a redirected Windows console (cp1252) cannot encode it. Replace
    # what the console cannot show instead of crashing after the turn already ran.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")
    try:
        agent = Agent.from_settings()
    except AgentPlatformError as exc:
        print(f"Configuration error: {exc.detail}", file=sys.stderr)
        return 2
    try:
        return asyncio.run(_run(agent, args))
    except AgentPlatformError as exc:
        print(exc.public_message, file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"File not found: {exc.filename}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
