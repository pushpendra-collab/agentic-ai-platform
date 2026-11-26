"""Deterministic business date ranges (Phase 11, ADR-060).

The LLM never computes a date. A relative expression ("aaj", "kal", "this month") goes
into a tool as a `DatePeriod` token. The service turns the token into a
`BusinessDateRange` from the injected clock and the business time zone:

| Period       | start (inclusive)            | end (exclusive)            |
|--------------|------------------------------|----------------------------|
| `today`      | today                        | today + 1                  |
| `yesterday`  | today - 1                    | today                      |
| `this_week`  | Monday of this week          | today + 1 (week to date)   |
| `last_week`  | Monday of last week          | Monday of this week        |
| `this_month` | 1st of this month            | today + 1 (month to date)  |
| `last_month` | 1st of last month            | 1st of this month          |
| `this_year`  | 1 January of this year       | today + 1 (year to date)   |
| `last_year`  | 1 January of last year       | 1 January of this year     |

Current periods run *to date*: days after today have no business yet. Years are calendar
years under the rule `year_is_calendar_year` (Phase 16), or Indian financial years (1 April
to 31 March, the table's "1 January" read as "1 April") under `year_is_financial_year_april`
(2026-10-30). The ERP contract confirms one of them; without either, year questions answer
RULE_NOT_VERIFIED.

Phase 16: `same_period_last_year(span)` gives the comparison range of a resolved range:
the same calendar dates one year earlier, 29 February mapped to 28 February (rule
`same_period_last_year_by_date`). A to-date period is compared with the same days of
last year (1-10 October against 1-10 October), never with the whole earlier month. "Today" is the
date in the business time zone. That zone is an ERP rule nobody has confirmed (Q1).
An adapter serving TEST data marks it a TEST zone (`timezone_source="TEST"`). It says nothing
about the production ERP. `ERP_BUSINESS_TIMEZONE` stays unresolved for the real ERP.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class DatePeriod(StrEnum):
    TODAY = "today"
    YESTERDAY = "yesterday"
    THIS_WEEK = "this_week"
    LAST_WEEK = "last_week"
    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"
    THIS_YEAR = "this_year"  # Phase 16
    LAST_YEAR = "last_year"  # Phase 16


# Synonyms accepted from tool inputs and the planner; normalised before validation.
PERIOD_ALIASES: dict[str, DatePeriod] = {
    "current_day": DatePeriod.TODAY,
    "current_week": DatePeriod.THIS_WEEK,
    "previous_week": DatePeriod.LAST_WEEK,
    "current_month": DatePeriod.THIS_MONTH,
    "previous_month": DatePeriod.LAST_MONTH,
    "current_year": DatePeriod.THIS_YEAR,
    "previous_year": DatePeriod.LAST_YEAR,
}

SINGLE_DAY_PERIODS = frozenset({DatePeriod.TODAY, DatePeriod.YESTERDAY})

# Relative period words in the user's own text (Hinglish, English, Hindi), most specific
# first. Used only to replace a date the model computed itself (see agents/domains/
# business.py); the period is then resolved by the clock like any other token.
_THIS, _LAST = r"(?:is|iss|this)", r"(?:pichh?l[ea]|pichh?le|last|previous)"
_PERIOD_WORDS: tuple[tuple[str, DatePeriod], ...] = (
    (rf"\b{_LAST}\s+(?:hafte|hafta|week)\b", DatePeriod.LAST_WEEK),
    (rf"\b{_THIS}\s+(?:hafte|hafta|week)\b", DatePeriod.THIS_WEEK),
    (rf"\b{_LAST}\s+(?:mahine|mahina|month)\b", DatePeriod.LAST_MONTH),
    (rf"\b{_THIS}\s+(?:mahine|mahina|month)\b", DatePeriod.THIS_MONTH),
    (rf"\b{_LAST}\s+(?:saal|sal|year)\b", DatePeriod.LAST_YEAR),
    (rf"\b{_THIS}\s+(?:saal|sal|year)\b", DatePeriod.THIS_YEAR),
    (r"\b(?:kal|yesterday)\b|(?<!\S)कल(?!\S)", DatePeriod.YESTERDAY),
    (r"\b(?:aaj|aj|today)\b|(?<!\S)आज(?!\S)", DatePeriod.TODAY),
)


def period_from_text(text: str) -> DatePeriod | None:
    """The one relative period the text names ("kal" -> yesterday), or None when it
    names none or several different ones."""
    lowered = text.casefold()
    found = {period for pattern, period in _PERIOD_WORDS if re.search(pattern, lowered)}
    return found.pop() if len(found) == 1 else None


WEEK_PERIODS = frozenset({DatePeriod.THIS_WEEK, DatePeriod.LAST_WEEK})
YEAR_PERIODS = frozenset({DatePeriod.THIS_YEAR, DatePeriod.LAST_YEAR})

# Phase 16: what a sales figure is compared with. One basis exists.
ComparisonBasis = Literal["same_period_last_year"]

# Where the zone that defined "today" came from. None: an explicit calendar date the
# user named, so no time zone was involved.
TimezoneSource = Literal["ERP_BUSINESS_TIMEZONE", "TEST"]


def normalise_period(value: object) -> object:
    """`before` validator helper: 'Current Month' / 'current-month' → 'this_month'."""
    if isinstance(value, str):
        key = value.strip().lower().replace(" ", "_").replace("-", "_")
        return PERIOD_ALIASES.get(key, key)
    return value


class BusinessDateRange(BaseModel):
    """A half-open range of business dates: `start` ≤ day < `end`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: date
    end: date
    # end - 1 day, stated explicitly so a reply that quotes it is sourced.
    last_day: date
    timezone: str | None
    timezone_source: TimezoneSource | None
    period: DatePeriod | None = None
    semantics: Literal["start_inclusive_end_exclusive"] = "start_inclusive_end_exclusive"

    @model_validator(mode="after")
    def _consistent(self) -> BusinessDateRange:
        if self.start >= self.end:
            raise ValueError("a date range holds at least one day")
        if self.last_day != self.end - timedelta(days=1):
            raise ValueError("last_day is the day before end")
        if (self.timezone is None) != (self.timezone_source is None):
            raise ValueError("timezone and timezone_source are set together")
        return self

    @property
    def days(self) -> int:
        return (self.end - self.start).days

    def contains(self, day: date) -> bool:
        return self.start <= day < self.end


def year_start(today: date, year_start_month: int = 1) -> date:
    """First day of the year containing `today`: 1 January, or (`year_start_month=4`) 1
    April of the financial year that holds `today` (Indian FY: April to March)."""
    if today.month >= year_start_month:
        return date(today.year, year_start_month, 1)
    return date(today.year - 1, year_start_month, 1)


def period_bounds(period: DatePeriod, today: date, year_start_month: int = 1) -> tuple[date, date]:
    """(start, end) of `period` relative to `today`. Pure; see the module table. Years
    start in `year_start_month` (1: calendar year; 4: Indian financial year)."""
    tomorrow = today + timedelta(days=1)
    monday = today - timedelta(days=today.weekday())
    first = today.replace(day=1)
    if period is DatePeriod.TODAY:
        return today, tomorrow
    if period is DatePeriod.YESTERDAY:
        return today - timedelta(days=1), today
    if period is DatePeriod.THIS_WEEK:
        return monday, tomorrow
    if period is DatePeriod.LAST_WEEK:
        return monday - timedelta(days=7), monday
    if period is DatePeriod.THIS_MONTH:
        return first, tomorrow
    if period is DatePeriod.LAST_MONTH:
        return (first - timedelta(days=1)).replace(day=1), first
    if period is DatePeriod.THIS_YEAR:
        return year_start(today, year_start_month), tomorrow
    if period is DatePeriod.LAST_YEAR:
        this_year = year_start(today, year_start_month)
        return this_year.replace(year=this_year.year - 1), this_year
    raise ValueError(f"unknown period {period!r}")  # pragma: no cover - closed enum


def resolve_period(
    period: DatePeriod,
    *,
    today: date,
    timezone: str,
    source: TimezoneSource,
    year_start_month: int = 1,
) -> BusinessDateRange:
    start, end = period_bounds(period, today, year_start_month)
    return BusinessDateRange(
        start=start,
        end=end,
        last_day=end - timedelta(days=1),
        timezone=timezone,
        timezone_source=source,
        period=period,
    )


def single_day(
    day: date,
    *,
    timezone: str | None = None,
    source: TimezoneSource | None = None,
    period: DatePeriod | None = None,
) -> BusinessDateRange:
    return BusinessDateRange(
        start=day,
        end=day + timedelta(days=1),
        last_day=day,
        timezone=timezone,
        timezone_source=source,
        period=period,
    )


def year_earlier(day: date) -> date:
    """The same calendar date one year earlier; 29 February becomes 28 February."""
    if day.month == 2 and day.day == 29:
        return date(day.year - 1, 2, 28)
    return day.replace(year=day.year - 1)


def same_period_last_year(span: BusinessDateRange) -> BusinessDateRange:
    """The comparison range of `span` (Phase 16): its first and last day each moved one
    year back. Pure. The day counts differ only around 29 February, and the result
    states both ranges, so a reply can say so."""
    start, last = year_earlier(span.start), year_earlier(span.last_day)
    return BusinessDateRange(
        start=start,
        end=last + timedelta(days=1),
        last_day=last,
        timezone=span.timezone,
        timezone_source=span.timezone_source,
        period=None,
    )
