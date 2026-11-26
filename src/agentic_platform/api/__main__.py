"""Serve the HTTP API with uvicorn, print its OpenAPI schema, or check the configuration.

    python -m agentic_platform.api                 # API_HOST:API_PORT (default 127.0.0.1:8000)
    python -m agentic_platform.api --openapi > openapi.json
    python -m agentic_platform.api --check-config  # production policy report, exit 1 on errors
    python -m agentic_platform.api --version       # build information (JSON), no configuration

One worker process per invocation (the agent, engine and Redis pools are per
process). Scale out with more processes behind a load balancer; Redis is then
required for the cross-process thread lock (ADR-031).

This is the supported production entry point (Phase 7, ADR-047): with
`APP_ENV=production` it refuses to start while the production policy
(`core/production.py`) reports an error, and logs its warnings. On SIGTERM uvicorn
stops accepting connections, lets in-flight requests finish for up to
`API_GRACEFUL_SHUTDOWN_SECONDS`, cancels the rest, then runs the app shutdown: the
bounded sequence of services/lifecycle.py (Phase 15) within
`API_SHUTDOWN_CLEANUP_SECONDS`. uvicorn then re-raises the signal it caught, so after
SIGTERM the process exits with the signal's status (143 in a container), which
orchestrators treat as a normal stop.

Exit codes: 0 normal end / printed report, 1 `--check-config` found errors,
2 configuration refused (nothing started).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from agentic_platform.core.build import build_info
from agentic_platform.core.config import AppSettings, get_settings
from agentic_platform.core.errors import AgentPlatformError
from agentic_platform.core.logging import configure_logging, get_logger
from agentic_platform.core.production import check_configuration, enforce_production_policy

logger = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentic_platform.api")
    parser.add_argument("--openapi", action="store_true", help="Print the OpenAPI schema.")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Print the production configuration report (no values) and exit.",
    )
    parser.add_argument("--host", help="Override API_HOST.")
    parser.add_argument("--port", type=int, help="Override API_PORT.")
    parser.add_argument("--version", action="store_true", help="Print the build information.")
    args = parser.parse_args(argv)
    if args.version:
        print(build_info().model_dump_json())
        return 0

    try:
        settings = get_settings()
    except ValueError as exc:  # pydantic ValidationError: names the setting, not its value
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.logging)

    if args.check_config:
        report = check_configuration(settings, role="api")
        print(report.model_dump_json(indent=2))
        return 1 if report.errors else 0

    from agentic_platform.api.app import create_app

    try:
        report = enforce_production_policy(settings, role="api")
        for issue in report.warnings:
            logger.warning("config.production.warning", extra={"fields": issue.model_dump()})
        app = create_app(settings)
    except AgentPlatformError as exc:
        print(f"Configuration error: {exc.detail}", file=sys.stderr)
        return 2
    if args.openapi:
        print(json.dumps(app.openapi(), indent=2))
        return 0

    import uvicorn

    uvicorn.run(app, **uvicorn_options(settings, host=args.host, port=args.port))
    return 0


def uvicorn_options(
    settings: AppSettings, *, host: str | None = None, port: int | None = None
) -> dict[str, Any]:
    return {
        "host": host or settings.api.host,
        "port": port or settings.api.port,
        "access_log": False,  # RequestContextMiddleware logs every request (no query strings)
        "log_level": settings.logging.level.lower(),
        "server_header": False,
        "date_header": False,
        "timeout_graceful_shutdown": settings.api.graceful_shutdown_seconds,
        # Phase 8 (ADR-054): X-Forwarded-For/-Proto only from the trusted gateway.
        "proxy_headers": settings.api.proxy_headers,
        "forwarded_allow_ips": settings.api.forwarded_allow_ips,
    }


if __name__ == "__main__":
    raise SystemExit(main())
