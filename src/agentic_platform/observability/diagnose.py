"""Rebuild one request's diagnostics from JSON logs.

    python -m agentic_platform.observability.diagnose --request-id <id> [--json] < agent.log
    python -m agentic_platform.observability.diagnose --request-id <id> --file agent.log

Reads only observability events (`"kind": "observability"`) of that request; every other
log line is ignored. Output holds ids, statuses, codes, counts and durations only.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from agentic_platform.observability.diagnostics import TurnDiagnostics, events_from_log_lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--file", help="log file (default: standard input)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = parser.parse_args(argv)
    if args.file:
        with open(args.file, encoding="utf-8", errors="replace") as handle:
            events = events_from_log_lines(handle, args.request_id)
    else:
        events = events_from_log_lines(sys.stdin, args.request_id)
    if not events:
        print(f"No observability events for request {args.request_id}.", file=sys.stderr)
        return 1
    diagnostics = TurnDiagnostics.from_events(events)
    if args.json:
        print(json.dumps(diagnostics.to_dict(), indent=2, default=str))
    else:
        print(diagnostics.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
