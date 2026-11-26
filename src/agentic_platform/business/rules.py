"""Business rules that a derived figure depends on (Phase 11, ADR-060).

A business read returns what the ERP states. Some answers need one more step, and that
step is itself a business rule: is an open invoice *overdue*? Is a period's sales the
sum of its days? Where does a week start? Which day is "today"? The platform never
decides these, and neither does the LLM. Each one is a named rule:

* an adapter declares the rules its mapping vouches for (`ErpReadPort.verified_rules`).
  The postgres adapter declares exactly the rules its VERIFIED contract confirms (Q1);
* `BUSINESS_TIMEZONE` holds when `ERP_BUSINESS_TIMEZONE` is configured.

A figure whose rule does not hold is never computed. The service leaves it out and
reports the rule as not verified, and the reply says so (RULE_NOT_VERIFIED). Rules that
an adapter applies *inside* a read (what counts as open, which documents count as sales,
which quantity is stock) stay in `Capability.rule_to_verify`. They belong to the mapping.
"""

from __future__ import annotations

from enum import StrEnum


class BusinessRule(StrEnum):
    # The business day boundary: ERP_BUSINESS_TIMEZONE is configured.
    BUSINESS_TIMEZONE = "business_timezone"
    # An open invoice is overdue when its due date is before the business date.
    OVERDUE_BY_DUE_DATE = "overdue_by_due_date"
    # The sales of a multi-day period are the sum of its business days' sales.
    SALES_PERIOD_IS_SUM_OF_DAYS = "sales_period_is_sum_of_days"
    # Weeks start on Monday (ISO 8601) for "this week" / "last week".
    WEEK_STARTS_MONDAY = "week_starts_monday"
    # Phase 16: "this year" / "last year" are calendar years (1 January to 31 December),
    # not a financial year (in India often April to March). The ERP owner must confirm it.
    YEAR_IS_CALENDAR_YEAR = "year_is_calendar_year"
    # 2026-10-30: "this year" / "last year" are Indian financial years (1 April to 31
    # March). Exclusive with YEAR_IS_CALENDAR_YEAR; a contract confirming both is refused.
    YEAR_IS_FINANCIAL_YEAR_APRIL = "year_is_financial_year_april"
    # Phase 16: the comparison period of "same period last year" is the same calendar
    # dates one year earlier (29 February maps to 28 February), with the same number of
    # days for a period that runs to date.
    SAME_PERIOD_LAST_YEAR_BY_DATE = "same_period_last_year_by_date"
