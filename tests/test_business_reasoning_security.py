"""Phase 11: security of the business reasoning layer, on the real ERP read path
(InvenLogic readers on the TEST replica, tests/erp_db.py; the fake ERP was removed on
2026-10-30).

Every Phase 11 addition (new tool arguments, intents, the conversation focus, warehouse
and product filters, sales periods) is held to the Phase 10 boundary: identity and scope
come only from the authenticated principal, natural language never authorizes, nothing
takes SQL, no adapter serving made-up data can be configured, no write tool exists. Each case must
fail safely: refused input, UNAUTHORIZED, or NOT_FOUND (existence never revealed).
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from agentic_platform.agents.domains.base import DomainRequest
from agentic_platform.agents.domains.business import BusinessDomainHandler
from agentic_platform.business.adapters.invenlogic import (
    CustomerOpenInvoicesReader,
    CustomerOutstandingReader,
    DailySalesReader,
    InvenLogicCore,
    LowStockReader,
    ProductStockReader,
    WarehouseStockReader,
)
from agentic_platform.business.adapters.postgres import PostgresErpAdapter
from agentic_platform.business.context import ScopeKind, WarehouseScope
from agentic_platform.business.dates import DatePeriod
from agentic_platform.business.intent import BusinessQueryIntent
from agentic_platform.business.ports import ErpReadPort
from agentic_platform.business.results import BusinessStatus as S
from agentic_platform.business.results import Reason
from agentic_platform.core.config import ErpSettings
from agentic_platform.core.errors import ToolRegistrationError
from agentic_platform.security import Principal
from agentic_platform.tools import ToolKind, ToolRegistry, ToolResult
from agentic_platform.tools.base import Tool, ToolInput, ToolOutput, ToolSpec
from agentic_platform.tools.business import BUSINESS_DOMAIN, register_business_tools
from tests.business_helpers import (
    TZ,
    ErpFaults,
    business_principal,
    erp_port,
    ext,
    globex,
    make_service,
    run,
)
from tests.conftest import understanding


def acme_user() -> Principal:  # the extended TEST company (the old tests' "acme")
    return business_principal(ext().tenant)


def globex_user() -> Principal:  # a second company on the same replica
    return business_principal(tenant_id=globex().tenant, user_id="gita")


class OnlyGodown1:
    """A listed warehouse scope, as a later phase's resolver would supply it."""

    def resolve(self, principal: Principal) -> WarehouseScope:
        return WarehouseScope(kind=ScopeKind.LISTED, warehouse_refs=frozenset({ext()["godown1"]}))


def setup(**kw: Any) -> tuple[Any, ToolRegistry, ErpFaults]:
    ext(), globex()  # both companies exist before the first read
    adapter = erp_port()
    service, _ = make_service(adapter=adapter, **kw)
    registry = ToolRegistry()
    register_business_tools(
        registry, service, ErpSettings(adapter="postgres", business_timezone=TZ)
    )
    return service, registry, adapter


def call(
    registry: ToolRegistry, name: str, args: dict[str, Any], who: Principal | None = None
) -> ToolResult:
    return run(
        registry.execute(
            f"business.{name}",
            json.dumps(args),
            allowed_domains=(BUSINESS_DOMAIN,),
            principal=who or acme_user(),
        )
    )


def status_of(result: ToolResult) -> str:
    assert result.status == "ok", result.error_code
    return str(result.output["result"]["status"])  # type: ignore[index]


# =============================================================================================
# tenant, warehouse, SQL and permission overrides on the new arguments
# =============================================================================================


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("customer_open_invoices", {"tenant_id": "globex"}),
        ("customer_open_invoices", {"invoice_number": "1; DROP TABLE invoices"}),
        ("customer_open_invoices", {"invoice_number": "x", "sql": "SELECT * FROM invoices"}),
        ("daily_sales", {"period": "this_month", "tenant_id": "globex"}),
        ("daily_sales", {"period": "this_month", "day": "2026-09-01"}),  # both
        ("daily_sales", {"period": "1=1"}),
        ("daily_sales", {"period": "next_year"}),
        ("product_stock", {"product_name": "iPhone 15", "warehouse_scope": "all"}),
        ("product_stock", {"product_name": "iPhone 15", "warehouses": ["2"]}),
        (
            "product_stock",
            {
                "product_name": "iPhone 15",
                "warehouse_name": "Godown 2",
                "warehouse_ref": "2",
            },
        ),
        ("product_stock", {"product_name": "iPhone 15", "table": "stock", "column": "qty"}),
        ("low_stock_products", {"product_name": "iPhone 15", "permissions": ["x"]}),
        ("low_stock_products", {"product_ref": "1' OR '1'='1"}),
    ],
)
def test_new_arguments_refuse_identity_scope_and_sql(tool: str, args: dict[str, Any]) -> None:
    _, registry, adapter = setup()
    result = call(registry, tool, args)
    assert (result.status, result.error_code) == ("error", "TOOL_INVALID_INPUT")
    assert adapter.calls == []


def test_naming_an_out_of_scope_warehouse_is_not_found() -> None:
    """'Godown 2 ka stock batao' from a user who may see only Godown 1."""
    _, registry, _adapter = setup(scopes=OnlyGodown1())
    limited = business_principal(ext().tenant, roles=("member", "stock_viewer_limited"))
    for args in (
        {"product_name": "iPhone 15", "warehouse_name": "Godown 2"},
        {"product_name": "iPhone 15", "warehouse_ref": ext()["godown2"]},
    ):
        assert status_of(call(registry, "product_stock", args, limited)) == "NOT_FOUND"
    visible = call(
        registry,
        "product_stock",
        {"product_name": "iPhone 15", "warehouse_name": "Godown 1"},
        limited,
    )
    assert Decimal(visible.output["result"]["data"]["total_quantity"]) == 12  # type: ignore[index]
    assert (
        status_of(call(registry, "warehouse_stock", {"warehouse_name": "Godown 2"}, limited))
        == "NOT_FOUND"
    )
    low = call(registry, "low_stock_products", {"product_name": "iPhone 15"}, limited)
    assert low.output["result"]["data"]["items"] == []  # type: ignore[index]  # low only in Godown 2


def test_without_any_warehouse_scope_stock_filters_are_unauthorized() -> None:
    _, registry, adapter = setup()
    no_scope = business_principal(ext().tenant, roles=("member", "stock_viewer_limited"))
    result = call(
        registry,
        "product_stock",
        {"product_name": "iPhone 15", "warehouse_name": "Godown 1"},
        no_scope,
    )
    assert result.output["result"]["reason"] == Reason.WAREHOUSE_SCOPE_EMPTY  # type: ignore[index]
    assert adapter.calls == []


@pytest.mark.parametrize(
    ("tool", "args", "roles"),
    [
        ("customer_open_invoices", {}, ("member", "stock_viewer")),
        ("customer_open_invoices", {"invoice_number": "TEST-INV-001"}, ("member",)),
        ("daily_sales", {"period": "last_month"}, ("member", "stock_viewer")),
        (
            "product_stock",
            {"product_name": "iPhone 15", "warehouse_name": "Godown 1"},
            ("member", "sales_viewer"),
        ),
        ("low_stock_products", {"product_name": "iPhone 15"}, ("member", "sales_viewer")),
    ],
)
def test_permission_is_checked_for_every_new_path(
    tool: str, args: dict[str, Any], roles: tuple[str, ...]
) -> None:
    _, registry, adapter = setup()
    result = call(registry, tool, args, business_principal(ext().tenant, roles=roles))
    assert result.error_code == "PERMISSION_DENIED"
    assert adapter.calls == []


def test_the_service_rechecks_permission_on_new_paths() -> None:
    service, _, adapter = setup()
    member = business_principal(ext().tenant, roles=("member",))
    results = [
        run(service.customer_open_invoices(member)),
        run(service.daily_sales(member, period=DatePeriod.THIS_WEEK)),
        run(service.low_stock_products(member, product_name="iPhone 15")),
    ]
    assert {(r.status, r.reason) for r in results} == {(S.UNAUTHORIZED, Reason.PERMISSION_MISSING)}
    assert adapter.calls == []


# =============================================================================================
# cross-tenant lookups
# =============================================================================================


def test_tenant_wide_lists_never_include_another_tenant() -> None:
    service, _, _ = setup()
    acme = run(service.customer_open_invoices(acme_user())).data
    other = run(service.customer_open_invoices(globex_user())).data
    # Each company sees only its own open bills (globex has one opening bill).
    assert acme.invoice_count == 4 and other.invoice_count == 1
    assert {i.customer and i.customer.ref for i in other.invoices} == {globex()["globex"]}
    assert globex()["globex"] not in {i.customer and i.customer.ref for i in acme.invoices}
    assert (
        run(service.daily_sales(globex_user(), period=DatePeriod.THIS_WEEK)).status is S.NOT_FOUND
    )


@pytest.mark.parametrize(
    "lookup",
    [
        lambda s: s.customer_open_invoices(globex_user(), customer_ref=ext()["rahul"]),
        lambda s: s.customer_open_invoices(globex_user(), invoice_number="TEST-INV-001"),
        lambda s: s.product_stock(
            globex_user(), product_name="iPhone 15", warehouse_name="Godown 1"
        ),
        lambda s: s.low_stock_products(globex_user(), product_ref=ext()["iphone"]),
        lambda s: s.customer_outstanding(acme_user(), customer_ref=globex()["globex"]),  # globex's
    ],
)
def test_another_tenants_reference_is_not_found(lookup: Any) -> None:
    service, _, _ = setup(timezone="Asia/Kolkata")
    # The caller holds every business permission: only the tenant boundary refuses.
    assert run(lookup(service)).status is S.NOT_FOUND


def test_a_tenant_leak_on_the_new_reads_is_discarded() -> None:
    service, _, adapter = setup()
    adapter.failure = "tenant_leak"
    for result in (
        run(service.customer_open_invoices(acme_user())),
        run(service.daily_sales(acme_user(), period=DatePeriod.LAST_MONTH)),
    ):
        assert (result.status, result.reason, result.data) == (
            S.DEPENDENCY_ERROR,
            Reason.TENANT_MISMATCH,
            None,
        )


def test_a_focus_reference_from_another_tenant_grants_nothing() -> None:
    """Even a tampered checkpoint focus only yields refs; the service resolves them
    inside the caller's tenant."""
    _service, registry, _adapter = setup()
    handler = BusinessDomainHandler(registry, max_tool_calls=3)
    query = BusinessQueryIntent(
        capability="customer_outstanding", customer_reference={"ref": globex()["globex"]}
    )  # type: ignore[arg-type]
    request = DomainRequest(
        request_id="r",
        thread_id="t",
        user_input="uska outstanding?",
        understanding=understanding(intent="x", domain="business").model_copy(
            update={"business_query": query}
        ),
        principal=acme_user(),
    )
    outcome = run(handler.handle(request))
    [result] = outcome.tool_results
    assert result.output["result"]["status"] == "NOT_FOUND"  # type: ignore[index]


# =============================================================================================
# no write path, no SQL path, production
# =============================================================================================


def test_a_write_intent_is_never_compiled_or_run() -> None:
    _, registry, adapter = setup()
    handler = BusinessDomainHandler(registry, max_tool_calls=3)
    query = BusinessQueryIntent(capability="low_stock_products", write_requested=True)
    request = DomainRequest(
        request_id="r",
        thread_id="t",
        user_input="PO bana do",
        understanding=understanding(intent="po", domain="business").model_copy(
            update={"business_query": query}
        ),
        principal=acme_user(),
    )
    assert run(handler.handle(request)).tool_results == []
    assert adapter.calls == []


def test_registering_a_business_write_tool_is_refused() -> None:
    service, _, _ = setup()

    async def handler(_: ToolInput) -> ToolOutput:
        return ToolOutput()

    write = Tool(
        ToolSpec(
            name="business.create_purchase_order",
            description="x",
            domain=BUSINESS_DOMAIN,
            kind=ToolKind.WRITE,
            input_model=ToolInput,
            output_model=ToolOutput,
            required_permission="business.inventory.read",
        ),
        handler,
    )
    import agentic_platform.tools.business as module

    original = module.business_tools
    module.business_tools = lambda s, c: [*original(s, c), write]  # type: ignore[assignment]
    try:
        with pytest.raises(ToolRegistrationError):
            register_business_tools(
                ToolRegistry(), service, ErpSettings(adapter="postgres", business_timezone=TZ)
            )
    finally:
        module.business_tools = original  # type: ignore[assignment]


def test_the_port_and_service_still_have_no_write_or_sql_method() -> None:
    from agentic_platform.business.service import BusinessReadService

    verbs = (
        "create",
        "update",
        "delete",
        "insert",
        "write",
        "save",
        "post",
        "adjust",
        "cancel",
        "pay",
        "execute",
        "sql",
        "raw",
        "send",
        "approve",
        "reorder",
        "settle",
    )
    readers = (
        InvenLogicCore, CustomerOutstandingReader, CustomerOpenInvoicesReader, DailySalesReader,
        ProductStockReader, WarehouseStockReader, LowStockReader,
    )  # fmt: skip
    for cls in (BusinessReadService, ErpReadPort, PostgresErpAdapter, *readers):
        names = [n for n in dir(cls) if not n.startswith("_")]
        assert not [n for n in names if any(v in n.lower() for v in verbs)], cls


def test_no_adapter_serving_made_up_data_can_be_configured() -> None:
    """The fake TEST-data adapter was removed: no environment can select it any more,
    and the live port never labels its data as TEST data."""
    with pytest.raises(ValidationError):
        ErpSettings(adapter="fake")  # type: ignore[arg-type]
    assert PostgresErpAdapter.test_data is False
    assert erp_port().test_data is False


def test_the_live_adapter_vouches_for_no_rule_and_answers_rule_not_verified() -> None:
    assert PostgresErpAdapter.verified_rules == frozenset()

    class _Never:  # the adapter must not touch its database for unmapped reads
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"database used: {name}")

    # No contract: no reader can be registered, so nothing may reach the database.
    service, _ = make_service(
        adapter=ErpFaults(PostgresErpAdapter({"sales": _Never()}))  # type: ignore[dict-item]
    )
    results = [
        run(service.customer_open_invoices(acme_user())),
        run(service.daily_sales(acme_user(), period=DatePeriod.THIS_MONTH)),  # period rules missing
        run(service.low_stock_products(acme_user(), product_name="iPhone 15")),
        run(
            service.product_stock(acme_user(), product_name="iPhone 15", warehouse_name="Godown 1")
        ),
    ]
    assert {(r.status, r.reason) for r in results} == {(S.UNAVAILABLE, Reason.RULE_NOT_VERIFIED)}


def test_intent_compilation_cannot_smuggle_arguments() -> None:
    from agentic_platform.tools.business import tool_call_for

    query = BusinessQueryIntent(
        capability="product_stock",
        product_reference={"name": "iPhone 15"},  # type: ignore[arg-type]
        warehouse_reference={"name": "Godown 2"},
    )  # type: ignore[arg-type]
    compiled = tool_call_for(query)
    assert compiled is not None
    assert set(json.loads(compiled[1])) == {"product_name", "warehouse_name"}


def test_concurrent_tenants_do_not_mix() -> None:
    service, _, _ = setup()

    async def both() -> tuple[Any, Any]:
        return await asyncio.gather(
            service.customer_open_invoices(acme_user()),
            service.customer_open_invoices(globex_user()),
        )

    acme, other = run(both())
    assert (acme.data.invoice_count, other.data.invoice_count) == (4, 1)
    assert {i.customer and i.customer.ref for i in other.data.invoices} == {globex()["globex"]}
