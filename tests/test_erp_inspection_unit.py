"""Phase 9: `scripts/inspect_erp_security` decisions without a database."""

from __future__ import annotations

import pytest
from scripts.inspect_erp_security import (
    URL_ENV,
    Facts,
    InspectionRefused,
    Probe,
    TableRls,
    describe,
    evaluate,
    main,
    refuse_agent_database_url,
)


def safe_facts(**changes: object) -> Facts:
    facts = Facts(
        database="erp",
        server_version="16.4",
        current_user="erp_reader",
        session_user="erp_reader",
        transaction_read_only="on",
        has_alembic_version=False,
        role_default_read_only=True,
    )
    facts.readable_tables = [TableRls("public", "customer", True, False, 1, "id")]
    facts.tenant_settings = ["app.tenant_id"]
    facts.probes = [Probe("public.customer", op, "42501") for op in ("INSERT", "UPDATE", "DELETE")]
    for key, value in changes.items():
        setattr(facts, key, value)
    return facts


def statuses(facts: Facts) -> dict[str, str]:
    result: dict[str, str] = {}
    for finding in evaluate(facts):
        # several findings may share a check name (flyway); FAIL > WARN > others
        if result.get(finding.check) not in ("FAIL", "WARN"):
            result[finding.check] = finding.status
    return result


def test_least_privilege_read_only_role_has_no_failures() -> None:
    result = statuses(safe_facts())
    assert "FAIL" not in result.values()
    assert result["probe.writes_refused"] == "PASS"
    assert result["role.nosuperuser"] == result["role.nobypassrls"] == "PASS"
    assert result["privilege.no_ddl"] == "PASS"


@pytest.mark.parametrize(
    ("change", "check"),
    [
        ({"superuser": True}, "role.nosuperuser"),
        ({"bypass_rls": True}, "role.nobypassrls"),
        ({"create_role": True}, "role.attributes"),
        ({"memberships": [("pg_write_all_data", True)]}, "role.memberships"),
        ({"owned": ["relation public.customer"]}, "role.owns_nothing"),
        ({"owned": ["relation public.customer"]}, "privilege.no_ddl"),
        ({"writable_relations": ["public.customer"]}, "privilege.no_table_writes"),
        ({"create_schemas": ["public"]}, "privilege.no_ddl"),
        ({"database_create": True}, "privilege.no_ddl"),
        ({"updatable_sequences": ["public.customer_id_seq"]}, "privilege.sequences"),
        ({"transaction_read_only": "off"}, "session.read_only"),
        ({"probes": [Probe("public.customer", "DELETE", None)]}, "probe.writes_refused"),
    ],
)
def test_unsafe_role_fails(change: dict[str, object], check: str) -> None:
    assert statuses(safe_facts(**change))[check] == "FAIL"


def test_missing_evidence_is_a_warning_not_a_pass() -> None:
    result = statuses(
        safe_facts(
            probes=[],
            tenant_settings=[],
            role_default_read_only=False,
            usable_sequences=2,
            readable_tables=[TableRls("public", "item", False, False, 0, "id")],
        )
    )
    assert result["probe.writes_refused"] == "WARN"
    assert result["rls.enabled"] == "WARN"
    assert result["rls.tenant_settings"] == "WARN"
    assert result["role.default_read_only"] == "WARN"
    assert result["privilege.sequences"] == "WARN"
    assert result["flyway"] == "WARN"


def test_inconclusive_probe_is_not_reported_as_refused() -> None:
    result = statuses(safe_facts(probes=[Probe("public.customer", "INSERT", "23502")]))
    assert result["probe.writes_refused"] == "WARN"


def test_agent_database_url_is_refused_before_connecting() -> None:
    agent = "postgresql+asyncpg://agentic_app:secret@DB.internal:5432/agentic"
    with pytest.raises(InspectionRefused, match="DATABASE_URL"):
        refuse_agent_database_url(
            "postgresql://reader:x@db.internal/agentic", {"DATABASE_URL": agent}
        )
    # another database on the same server is fine
    refuse_agent_database_url("postgresql://reader:x@db.internal/erp", {"DATABASE_URL": agent})


def test_description_never_contains_the_password() -> None:
    text = describe("postgresql://reader:Sup3rS3cret@erp.internal:6432/erp")
    assert text == "erp.internal:6432/erp as reader"
    assert "Sup3rS3cret" not in text


def test_main_refuses_without_url_and_for_the_agent_db(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--expect-database", "erp"], environ={}) == 2
    url = "postgresql://reader:Sup3rS3cret@db.internal:5432/agentic"
    assert (
        main(
            ["--expect-database", "agentic"], environ={URL_ENV: url, "DATABASE_MIGRATION_URL": url}
        )
        == 2
    )
    assert main(["--expect-database", "erp"], environ={URL_ENV: "mysql://x@y/erp"}) == 2
    captured = capsys.readouterr()
    assert "Sup3rS3cret" not in captured.out + captured.err
    assert captured.err.count("REFUSED") == 3


def test_unreachable_server_reports_type_only(capsys: pytest.CaptureFixture[str]) -> None:
    url = "postgresql://reader:Sup3rS3cret@127.0.0.1:1/erp"  # nothing listens on port 1
    assert main(["--expect-database", "erp"], environ={URL_ENV: url}) == 2
    captured = capsys.readouterr()
    assert "NOT INSPECTED" in captured.err
    assert "Sup3rS3cret" not in captured.out + captured.err
    assert "127.0.0.1" not in captured.err
