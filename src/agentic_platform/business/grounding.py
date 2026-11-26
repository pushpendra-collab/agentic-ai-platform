"""Figure grounding for business replies (Phase 10, ADR-059).

The reply to a business question is written by the LLM, but its figures must come from
the deterministic ERP results, never from the model or from memory. After generation,
`verify_business_response` checks every number in the reply against the numbers in the
business tool results and in the user's own message. If any number has no source, the
reply is replaced by `render_results`, a deterministic text built from the results alone.

Numbers are compared after normalisation: thousands separators removed (Western and
Indian grouping), trailing decimal zeros dropped (`45,000.00` == `45000`). Limitations:
figures in words and short forms ("forty-five thousand", "45k", "1.25 lakh") are read by
`figures_in` (Phase 11; limits listed there); the prompt
forbids computing new figures, and the check fails closed on anything it does see.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from agentic_platform.tools.base import ToolResult

BUSINESS_TOOL_PREFIX = "business."
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def normalise(token: str) -> str | None:
    try:
        value = Decimal(token.replace(",", "").rstrip("."))
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    return format(value.normalize(), "f")


def numbers_in(text: str) -> set[str]:
    return {n for n in (normalise(m.group(0)) for m in _NUMBER.finditer(text)) if n is not None}


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)
    elif value is not None and not isinstance(value, bool):
        yield str(value)


def business_results(tool_results: Iterable[ToolResult]) -> list[dict[str, Any]]:
    """The `BusinessResult` payloads of successful business tool calls."""
    found: list[dict[str, Any]] = []
    for r in tool_results:
        if r.tool_name.startswith(BUSINESS_TOOL_PREFIX) and r.status == "ok" and r.output:
            result = r.output.get("result")
            if isinstance(result, dict):
                found.append(result)
    return found


def sourced_numbers(results: Iterable[Mapping[str, Any]], user_input: str) -> set[str]:
    allowed = numbers_in(user_input)
    for result in results:
        for text in _strings(result):
            allowed |= numbers_in(text)
    return allowed


def ungrounded_numbers(reply: str, allowed: set[str]) -> list[str]:
    return sorted(figures_in(reply) - allowed)


# --- percentages (Phase 16) ------------------------------------------------------------------
#
# A percentage is checked more strictly than other figures: it must equal a percentage
# the service computed (a result field whose name contains "percent") or one the user
# wrote. Otherwise "30%" would pass whenever 30 occurs anywhere in a result, for example
# as the day of a date ("2026-09-30").

_PERCENT = re.compile(
    r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s?(?:%|percent\b|per cent\b|pratishat\b|fees?di\b)",
    re.IGNORECASE,
)


def percentages_in(text: str) -> set[str]:
    return {n for n in (normalise(m.group(1)) for m in _PERCENT.finditer(text)) if n}


def _percent_values(value: Any, inside: bool = False) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _percent_values(item, inside or "percent" in str(key).lower())
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _percent_values(item, inside)
    elif inside and value is not None and not isinstance(value, bool):
        yield str(value)


def sourced_percentages(results: Iterable[Mapping[str, Any]], user_input: str) -> set[str]:
    allowed = percentages_in(user_input)
    for result in results:
        for text in _percent_values(result):
            allowed |= {n for n in (normalise(t) for t in numbers_in(text)) if n}
    return allowed


def ungrounded_percentages(reply: str, allowed: set[str]) -> list[str]:
    return sorted(percentages_in(reply) - allowed)


# --- figure forms in replies (Phase 11) ---------------------------------------------------
#
# A reply may write a figure in forms other than plain digits. `figures_in` reads, in
# addition to everything `numbers_in` reads:
#
# * a digit figure with a scale: "45k", "45K", "1.25L", "45 thousand", "45 hazaar",
#   "1.25 lakh", "2 crore", "3 million" (the digits are consumed: "45k" is 45000, not 45);
# * English number words with a tens or scale word: "forty-five thousand",
#   "one lakh twenty five thousand", "ninety".
#
# Known limits (documented in docs/BUSINESS_INTEGRATION.md § 6): a lone small number
# word ("one" to "nineteen" with no scale word) is not read, because "which one?" is not
# a figure; Hindi number words ("pachaas") are not read; a number that happens to occur
# elsewhere in a result (a date part, an invoice number) counts as sourced.

_SCALES = {
    "k": 1_000, "thousand": 1_000, "thousands": 1_000, "hazar": 1_000, "hazaar": 1_000,
    "hajar": 1_000, "l": 100_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000,
    "lacs": 100_000, "cr": 10_000_000, "crore": 10_000_000, "crores": 10_000_000,
    "million": 1_000_000, "millions": 1_000_000, "mn": 1_000_000, "billion": 1_000_000_000,
}  # fmt: skip
_SCALED = re.compile(
    r"(?<![\w.])(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?:(?P<attached>[kKL])(?![\w])|\s?(?P<word>thousands?|hazaa?r|hajar|lakhs?|lacs?|"
    r"crores?|cr|millions?|mn|billion)\b)",
    re.IGNORECASE,
)
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}  # fmt: skip
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip
_WORD_SCALES = {
    "hundred": 100, "thousand": 1_000, "lakh": 100_000, "lac": 100_000,
    "crore": 10_000_000, "million": 1_000_000, "billion": 1_000_000_000,
}  # fmt: skip
_WORD = re.compile(r"[a-z]+")


def _scaled_figures(text: str) -> tuple[set[str], str]:
    """Figures with a scale, and the text with them blanked out."""
    found: set[str] = set()

    def take(match: re.Match[str]) -> str:
        scale = (match.group("attached") or match.group("word") or "").lower()
        base = normalise(match.group("num"))
        if base is not None and scale in _SCALES:
            value = normalise(str(Decimal(base) * _SCALES[scale]))
            if value is not None:
                found.add(value)
        return " "

    return found, _SCALED.sub(take, text)


def _word_figures(text: str) -> set[str]:
    words = _WORD.findall(text.lower().replace("-", " "))
    found: set[str] = set()
    i = 0
    while i < len(words):
        if words[i] not in _UNITS and words[i] not in _TENS:
            i += 1
            continue
        total, current, significant, j = 0, 0, False, i
        while j < len(words):
            word = words[j]
            if word in _UNITS:
                current += _UNITS[word]
            elif word in _TENS:
                current += _TENS[word]
                significant = True
            elif word in _WORD_SCALES and (current or total):
                scale = _WORD_SCALES[word]
                significant = True
                if scale == 100:
                    current *= 100
                else:
                    total += current * scale
                    current = 0
            elif (
                word == "and"
                and j + 1 < len(words)
                and (words[j + 1] in _UNITS or words[j + 1] in _TENS)
            ):
                pass
            else:
                break
            j += 1
        if significant:
            found.add(str(total + current))
        i = max(j, i + 1)
    return found


def figures_in(text: str) -> set[str]:
    """Every figure a reply states: digits, scaled digits and English number words."""
    scaled, rest = _scaled_figures(text)
    return scaled | numbers_in(rest) | _word_figures(rest)


# --- hedging and action claims (Phase 11) -------------------------------------------------

_HEDGE = re.compile(
    r"\b(i think|i believe|i guess|maybe|perhaps|probably|possibly|might be|could be|"
    r"presumably|shayad|lagta hai|mujhe lagta|ho sakta hai)\b",
    re.IGNORECASE,
)
_ACTION_CLAIM = re.compile(
    r"\b(created|placed|raised|generated|submitted|approved|sent|cancelled|canceled|"
    r"settled|adjusted|booked|reordered)\b[^.!?\n]{0,60}"
    r"\b(purchase order|order|po|invoice|payment|stock|reorder|whatsapp|sms|message|entry)\b"
    r"|\b(purchase order|order|po|invoice|payment|stock|reorder|whatsapp|sms|message)\b"
    r"[^.!?\n]{0,40}\b(has been|have been|was|were|is now)\s+(created|placed|raised|"
    r"generated|submitted|approved|sent|cancelled|settled|adjusted|booked)\b"
    r"|\b(purchase order|order|po|invoice|payment|whatsapp|sms|message)\s+(sent|placed|"
    r"created|raised|approved|generated|booked|submitted)\b"
    r"|\b(bana|banaa|place|create|approve|bhej|send)\s+(diya|di|dia|diye|kar diya|kar di|"
    r"ho gaya|ho gayi)\b",
    re.IGNORECASE,
)


_PROFIT = re.compile(r"\b(profits?|munaf[a]+|margins?|net income|earnings|kamai)\b", re.IGNORECASE)
_NEGATED_PROFIT = re.compile(
    r"\b(not|no|never|nahi|nahin|na ki)\s+(the\s+|a\s+)?(profits?|munaf[a]+|margins?|"
    r"net income|earnings|kamai)\b"
    r"|\b(profits?|munaf[a]+|margins?|net income|earnings|kamai)\s+(nahi|nahin|not)\b",
    re.IGNORECASE,
)


def claims_profit(reply: str) -> bool:
    """True when the reply presents a figure as profit (Phase 16). The ERP reports sales
    totals; profit needs purchase cost and rules nobody has verified, so a sales answer
    never speaks of profit, margin or earnings ("sales, not profit" is fine)."""
    return _PROFIT.search(_NEGATED_PROFIT.sub(" ", reply)) is not None


def hedges(reply: str) -> bool:
    """True when the reply qualifies a business answer with a guess ("maybe", "shayad")."""
    return _HEDGE.search(reply) is not None


def claims_action(reply: str) -> bool:
    """True when the reply claims a business action happened (an order placed, a message
    sent). The business layer is read only, so such a claim is always false."""
    return _ACTION_CLAIM.search(reply) is not None


# --- deterministic rendering -------------------------------------------------------------


def _money(m: Mapping[str, Any] | None) -> str:
    if not m:
        return "not available"
    return f"{m['amount']} {m['currency']}"


def _name(ref: Mapping[str, Any]) -> str:
    return str(ref.get("display_name", ""))


def _success(capability: str, d: Mapping[str, Any]) -> str:
    if capability == "customer_outstanding":
        return f"Outstanding for {_name(d['customer'])}: {_money(d['outstanding'])}."
    if capability == "customer_open_invoices":
        lines = "; ".join(
            f"{_name(i['invoice'])} {_money(i['balance'])}" for i in d.get("invoices", [])
        )
        total = f" Total: {_money(d['total_balance'])}." if d.get("total_balance") else ""
        return f"Open invoices of {_name(d['customer'])}: {lines or 'none'}.{total}" + (
            " The list is incomplete." if d.get("truncated") else ""
        )
    if capability == "daily_sales":
        return (
            f"Sales on {d['business_date']}: {_money(d['total'])} "
            f"from {d['document_count']} document(s)."
        )
    if capability == "product_stock":
        lines = "; ".join(
            f"{_name(line['warehouse'])} {line['quantity']}" for line in d.get("lines", [])
        )
        unit = f" {d['unit']}" if d.get("unit") else ""
        scope = " (only the warehouses you can see)" if d.get("scope_limited") else ""
        return f"Stock of {_name(d['product'])}{scope}: {d['total_quantity']}{unit}" + (
            f" ({lines})." if lines else "."
        )
    if capability == "warehouse_stock":
        lines = "; ".join(f"{_name(p['product'])} {p['quantity']}" for p in d.get("lines", []))
        return f"Stock in {_name(d['warehouse'])}: {lines or 'nothing'}."
    if capability == "low_stock_products":
        items = "; ".join(
            f"{_name(i['product'])} in {_name(i['warehouse'])}: {i['quantity']} "
            f"(low-stock level {i['threshold']})"
            for i in d.get("items", [])
        )
        return f"Low-stock products: {items or 'none'}."
    if capability == "customer_count":
        if "date_range" in d:
            span = d["date_range"]
            return (
                f"Customers billed from {span['start']} to {span['last_day']}: "
                f"{d['customer_count']} ({d['invoice_count']} invoice(s)); counter-sale "
                f"bills: {d['counter_sale_count']}."
            )
        return f"Customers: {d['active_count']} active, {d['inactive_count']} inactive."
    return "The data was retrieved."


_OTHER = {
    "NOT_FOUND": "No matching record was found. Please check the name.",
    "UNAVAILABLE": "The current business data could not be retrieved right now.",
    "UNAUTHORIZED": "You do not have access to this information.",
    "VALIDATION_ERROR": "The question could not be answered as asked.",
    "DEPENDENCY_ERROR": "The business data could not be read correctly.",
}


def render_results(results: Iterable[Mapping[str, Any]]) -> str:
    """Plain text built only from the results (no LLM)."""
    parts: list[str] = []
    for result in results:
        status = result.get("status")
        if status == "SUCCESS" and isinstance(result.get("data"), Mapping):
            text = _success(str(result.get("capability")), result["data"])
            source = result.get("source") or {}
            if source.get("test_data"):
                text += " (TEST data, not real company data.)"
        elif status == "AMBIGUOUS":
            names = ", ".join(_name(c) for c in result.get("candidates", []))
            text = f"Several records match: {names}. Which one do you mean?"
        elif status == "UNAVAILABLE" and result.get("reason") in (
            "RULE_NOT_VERIFIED",
            "ERP_NOT_CONFIGURED",
        ):
            text = "This information is not available in the assistant yet."
        else:
            text = _OTHER.get(str(status), "The business data could not be retrieved.")
        parts.append(text)
    return " ".join(parts) or "The business data could not be retrieved."


def is_business_tool(result: ToolResult) -> bool:
    return result.tool_name.startswith(BUSINESS_TOOL_PREFIX)
