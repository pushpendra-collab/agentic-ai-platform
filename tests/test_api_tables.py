"""TurnResponse.tables: business lists as rows and columns for the chat page."""

from __future__ import annotations

from typing import Any

from agentic_platform.agents.schemas import AgentResponse
from agentic_platform.api.schemas import TurnResponse
from agentic_platform.tools.base import ToolResult


def _low_stock(items: list[dict[str, Any]], *, truncated: bool = False) -> dict[str, Any]:
    return {
        "result": {
            "capability": "low_stock_products",
            "status": "SUCCESS",
            "data": {
                "items": items,
                "item_count": len(items),
                "truncated": truncated,
                "scope_limited": False,
            },
        }
    }


def _item(product: str, warehouse: str, qty: str, level: str, unit: str | None) -> dict[str, Any]:
    return {
        "product": {"ref": "p", "display_name": product},
        "warehouse": {"ref": "w", "display_name": warehouse},
        "quantity": qty,
        "threshold": level,
        "unit": unit,
    }


def _response(
    *results: ToolResult, status: str = "completed", intent: str | None = None
) -> AgentResponse:
    return AgentResponse(
        request_id="r",
        thread_id="t",
        status=status,
        message="m",
        intent=intent,
        tool_results=list(results),
    )


def _ok(capability: str, data: dict[str, Any]) -> ToolResult:
    return ToolResult(
        tool_name=f"business.{capability}",
        status="ok",
        output={"result": {"capability": capability, "status": "SUCCESS", "data": data}},
    )


def _bill(number: str, customer: str | None, balance: str, overdue: bool | None) -> dict[str, Any]:
    return {
        "invoice": {"ref": "i", "display_name": number},
        "balance": {"amount": balance, "currency": "INR"},
        "issued_on": "2026-09-01",
        "due_on": "2026-10-01",
        "customer": {"ref": "c", "display_name": customer} if customer else None,
        "overdue": overdue,
    }


def test_low_stock_result_becomes_a_table() -> None:
    out = _low_stock(
        [
            _item("Fast Charger 25W (TEST)", "Shop Counter (TEST)", "22", "25.000", "pcs"),
            _item("OTG Adapter (TEST)", "Shop Counter (TEST)", "12.50", "24.000", "box"),
            _item("Cable (TEST)", "Pune Godown (TEST)", "9", "10", None),
        ],
        truncated=True,
    )
    view = TurnResponse.of(
        _response(ToolResult(tool_name="business.low_stock_products", status="ok", output=out))
    )
    assert len(view.tables) == 1
    table = view.tables[0]
    assert table.columns == ["Product", "Warehouse", "Stock", "Low-stock level"]
    assert table.rows == [
        ["Fast Charger 25W (TEST)", "Shop Counter (TEST)", "22 pcs", "25 pcs"],
        ["OTG Adapter (TEST)", "Shop Counter (TEST)", "12.50 box", "24 box"],
        ["Cable (TEST)", "Pune Godown (TEST)", "9", "10"],
    ]
    assert table.truncated is True


def test_no_table_without_a_successful_list() -> None:
    failed = ToolResult(tool_name="business.low_stock_products", status="error", error_code="X")
    unauthorized = ToolResult(
        tool_name="business.low_stock_products",
        status="ok",
        output={"result": {"capability": "low_stock_products", "status": "UNAUTHORIZED"}},
    )
    empty = ToolResult(tool_name="business.low_stock_products", status="ok", output=_low_stock([]))
    other = ToolResult(
        tool_name="business.daily_sales",
        status="ok",
        output={"result": {"capability": "daily_sales", "status": "SUCCESS", "data": {}}},
    )
    assert TurnResponse.of(_response(failed, unauthorized, empty, other)).tables == []


def test_no_table_when_the_turn_did_not_complete() -> None:
    ok = ToolResult(
        tool_name="business.low_stock_products",
        status="ok",
        output=_low_stock([_item("A", "B", "1", "2", "pcs")]),
    )
    assert TurnResponse.of(_response(ok, status="failed")).tables == []


def test_bills_of_all_customers_become_a_table_with_a_customer_column() -> None:
    data = {
        "customer": None,
        "invoices": [
            _bill("SI/2627/00001", "Nisha Yadav (TEST)", "142300.89", True),
            _bill("SI/2627/00002", "Amit Patel (TEST)", "5000.00", False),
        ],
        "overdue_count": 1,
        "truncated": False,
    }
    [table] = TurnResponse.of(_response(_ok("customer_open_invoices", data))).tables
    assert table.title == "Pending bills"
    assert table.columns == ["Bill no.", "Customer", "Date", "Due date", "Balance", "Overdue"]
    assert table.rows[0] == [
        "SI/2627/00001",
        "Nisha Yadav (TEST)",
        "2026-09-01",
        "2026-10-01",
        "₹1,42,300.89",
        "Yes",
    ]
    assert table.rows[1][4:] == ["₹5,000", "No"]


def test_an_overdue_question_lists_only_overdue_bills() -> None:
    data = {
        "customer": {"ref": "c", "display_name": "Nisha Yadav (TEST)"},
        "invoices": [
            _bill("SI/1", None, "100.00", True),
            _bill("SI/2", None, "200.00", False),
        ],
        "overdue_count": 1,
        "truncated": False,
    }
    response = _response(_ok("customer_open_invoices", data), intent="customer_overdue_invoices")
    [table] = TurnResponse.of(response).tables
    assert table.title == "Overdue bills of Nisha Yadav (TEST)"
    assert "Customer" not in table.columns
    assert [r[0] for r in table.rows] == ["SI/1"]


def test_overdue_is_not_guessed_when_the_service_could_not_decide() -> None:
    data = {
        "customer": None,
        "invoices": [_bill("SI/1", "A", "100.00", None)],
        "overdue_count": None,
        "truncated": True,
    }
    response = _response(_ok("customer_open_invoices", data), intent="overdue_bills")
    [table] = TurnResponse.of(response).tables
    assert table.title == "Overdue bills"
    assert table.rows[0][-1] == "-" and table.truncated is True


def test_warehouse_and_product_stock_tables() -> None:
    warehouse = {
        "warehouse": {"ref": "w", "display_name": "Main Godown (TEST)"},
        "lines": [{"product": {"ref": "p", "display_name": "Redmi 13C (TEST)"},
                   "quantity": "30.000", "unit": "pcs"}],
        "product_count": 1,
        "truncated": False,
    }  # fmt: skip
    product = {
        "product": {"ref": "p", "display_name": "Samsung Galaxy S24 (TEST)"},
        "unit": "pcs",
        "lines": [
            {"warehouse": {"ref": "a", "display_name": "Main Godown (TEST)"}, "quantity": "30"},
            {"warehouse": {"ref": "b", "display_name": "Shop Counter (TEST)"}, "quantity": "71"},
        ],
        "total_quantity": "101",
        "warehouse_count": 2,
        "scope_limited": False,
    }
    response = _response(_ok("warehouse_stock", warehouse), _ok("product_stock", product))
    stock, per_product = TurnResponse.of(response).tables
    assert (stock.title, stock.rows) == (
        "Stock in Main Godown (TEST)",
        [["Redmi 13C (TEST)", "30 pcs"]],
    )
    assert per_product.rows[-1] == ["Total", "101 pcs"]
    one = dict(product, lines=product["lines"][:1], warehouse_count=1)
    assert TurnResponse.of(_response(_ok("product_stock", one))).tables == []
