"""resolve_tool_name: a planned name without its domain prefix finds the one tool."""

from __future__ import annotations

from agentic_platform.agents.domains.base import resolve_tool_name
from agentic_platform.tools.base import ToolInput, ToolKind, ToolOutput, ToolSpec


def _spec(name: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="d",
        domain=name.split(".")[0],
        kind=ToolKind.READ,
        input_model=ToolInput,
        output_model=ToolOutput,
    )


SPECS = [_spec("business.daily_sales"), _spec("business.low_stock_products"), _spec("a.x"),
         _spec("b.x")]  # fmt: skip


def test_exact_names_are_kept() -> None:
    assert resolve_tool_name("business.daily_sales", SPECS) == "business.daily_sales"


def test_a_missing_prefix_is_added_when_one_tool_matches() -> None:
    assert resolve_tool_name("daily_sales", SPECS) == "business.daily_sales"


def test_unknown_partial_or_ambiguous_names_stay_as_given() -> None:
    assert resolve_tool_name("sales", SPECS) == "sales"  # no ".sales" suffix match
    assert resolve_tool_name("x", SPECS) == "x"  # two domains have it
    assert resolve_tool_name("profit", SPECS) == "profit"


def test_period_words_in_the_users_text() -> None:
    from agentic_platform.business.dates import DatePeriod as P
    from agentic_platform.business.dates import period_from_text

    cases = {
        "Kal ki sales batao": P.YESTERDAY,
        "aaj ka total": P.TODAY,
        "कल की बिक्री": P.YESTERDAY,
        "Is mahine ki sales": P.THIS_MONTH,
        "pichhle mahine ki sales": P.LAST_MONTH,
        "pichle hafte": P.LAST_WEEK,
        "is saal": P.THIS_YEAR,
        "last year sales": P.LAST_YEAR,
    }
    for text, period in cases.items():
        assert period_from_text(text) is period, text
    assert period_from_text("Pichhle saal se is saal compare karo") is None  # two periods
    assert period_from_text("Kalyan Traders ka balance") is None  # not the word "kal"
    assert period_from_text("Sales batao") is None
