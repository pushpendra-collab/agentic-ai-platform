"""The ERP statement guard (Phase 10 layer 4; its own module since Phase 14).

Only `ErpQuery` constants declared in code run against the ERP, and each must pass
`check_read_only_sql`. Moved out of `adapters/postgres.py` so the security gate
(`business/erp_gate.py`) can declare its catalog queries without an import cycle;
`adapters/postgres.py` re-exports both names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentic_platform.business.ports import ErpSafetyViolationError

# --- statement guard (layer 4) ---------------------------------------------------------

_STARTS_READ = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_FORBIDDEN_WORDS = re.compile(
    r"\b(insert|update|delete|merge|truncate|create|alter|drop|grant|revoke|copy|call|do|"
    r"lock|vacuum|analyze|reindex|cluster|refresh|comment|security|reset|set|notify|listen|"
    r"unlisten|prepare|execute|deallocate|discard|import|load|into)\b",
    re.IGNORECASE,
)
_LOCKING = re.compile(r"\bfor\s+(update|share|no\s+key|key)\b", re.IGNORECASE)
_FORBIDDEN_FUNCTIONS = re.compile(
    r"\b(nextval|setval|set_config|pg_(try_)?advisory\w*|dblink\w*|lo_\w+|"
    r"pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_rotate_logfile|pg_sleep\w*|"
    r"pg_notify|txid_current|pg_current_xact_id|pg_read_file|pg_read_binary_file|pg_ls_dir|"
    r"pg_stat_reset\w*|query_to_xml\w*)\s*\(",
    re.IGNORECASE,
)


def check_read_only_sql(sql: str) -> None:
    """Refuse anything but one plain read statement. Defence in depth: the read-only
    transaction and the read-only role are the real enforcement; this catches mistakes
    in code review before they reach the ERP. Fails closed (a forbidden word inside a
    string literal is refused too)."""
    problems: list[str] = []
    if not _STARTS_READ.match(sql):
        problems.append("must start with SELECT or WITH")
    if ";" in sql:
        problems.append("one statement only (no ';')")
    if "--" in sql or "/*" in sql:
        problems.append("no comments")
    if match := _FORBIDDEN_WORDS.search(sql):
        problems.append(f"forbidden keyword {match.group(1).upper()}")
    if match := _LOCKING.search(sql):
        problems.append(f"row locking ({match.group(0).upper()})")
    if match := _FORBIDDEN_FUNCTIONS.search(sql):
        problems.append(f"forbidden function {match.group(1)}")
    if problems:
        raise ErpSafetyViolationError("ERP query refused: " + "; ".join(problems))


@dataclass(frozen=True)
class ErpQuery:
    """A reviewed, parameterised read declared in code. Parameters are bound, never
    interpolated. Construction fails for anything that is not a plain read."""

    name: str
    sql: str

    def __post_init__(self) -> None:
        check_read_only_sql(self.sql)


__all__ = ["ErpQuery", "check_read_only_sql"]
