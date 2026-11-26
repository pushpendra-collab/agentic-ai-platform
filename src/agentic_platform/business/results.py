"""Deterministic result semantics of every business read (Phase 10, ADR-059).

A business read never raises into the agent and never returns a bare value. It returns
a `BusinessResult`: a status from a closed set, a machine-readable reason, the typed
data (SUCCESS only), the candidates (AMBIGUOUS only) and where the data came from.

| Status            | Meaning | The reply must    |
|-------------------|---------------------------------------------------------|-------------------|
| SUCCESS           | the ERP answered; `data` is authoritative | use `data` only   |
| NOT_FOUND         | nothing matched (also: exists but outside the caller's | say so            |
|                   | tenant or warehouse scope; existence is never revealed) |                   |
| AMBIGUOUS         | several records matched; `candidates` lists them | ask which one     |
| UNAVAILABLE       | the data cannot be retrieved now (ERP not configured, | say the current   |
|                   | unreachable, timed out, or the business rule behind the | data could not be |
|                   | question is not verified) | retrieved         |
| UNAUTHORIZED      | the caller may not read this | say so            |
| VALIDATION_ERROR  | the question is well-formed but not answerable as asked | say why           |
| DEPENDENCY_ERROR  | the ERP answered something unusable (malformed, wrong | say the data      |
|                   | tenant): the answer is discarded | could not be read |

Only SUCCESS carries figures. Nothing else may be turned into a figure, from memory or
otherwise (ADR-059; enforced by `agents/nodes/verify_business_response.py`).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentic_platform.business.models import EntityRef

T = TypeVar("T", bound=BaseModel)


class BusinessStatus(StrEnum):
    SUCCESS = "SUCCESS"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    UNAVAILABLE = "UNAVAILABLE"
    UNAUTHORIZED = "UNAUTHORIZED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"


class Reason:
    """Stable reason codes (strings, so adapters can add their own)."""

    ERP_NOT_CONFIGURED = "ERP_NOT_CONFIGURED"
    ERP_UNAVAILABLE = "ERP_UNAVAILABLE"
    ERP_TIMEOUT = "ERP_TIMEOUT"
    RULE_NOT_VERIFIED = "RULE_NOT_VERIFIED"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    PERMISSION_MISSING = "PERMISSION_MISSING"
    WAREHOUSE_SCOPE_EMPTY = "WAREHOUSE_SCOPE_EMPTY"
    FUTURE_DATE = "FUTURE_DATE"
    PERIOD_REQUIRED = "PERIOD_REQUIRED"  # Phase 16: a comparison names a relative period
    MIXED_CURRENCY = "MIXED_CURRENCY"
    NO_MATCH = "NO_MATCH"
    SEVERAL_MATCHES = "SEVERAL_MATCHES"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class Entity:
    """What a NOT_FOUND / AMBIGUOUS result refers to (Phase 11)."""

    CUSTOMER = "customer"
    INVOICE = "invoice"
    OUTSTANDING = "outstanding"
    SALES = "sales"
    PRODUCT = "product"
    WAREHOUSE = "warehouse"


# Retrying later can help only for these.
RETRYABLE_REASONS = frozenset({Reason.ERP_UNAVAILABLE, Reason.ERP_TIMEOUT})


class DataSource(BaseModel):
    """Provenance of a SUCCESS result: which system answered, through which adapter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    system: str = "erp"
    adapter: str
    retrieved_at: datetime
    # True for the fake adapter: the data is TEST data and must never be presented as real.
    test_data: bool = False


class BusinessResult(BaseModel, Generic[T]):
    """The outcome of one business read. Safe to show to the LLM (no tenant ids)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: str
    status: BusinessStatus
    reason: str | None = None
    retryable: bool = False
    data: T | None = None
    candidates: list[EntityRef] = Field(default_factory=list)
    source: DataSource | None = None
    # Phase 11: the entity a NOT_FOUND / AMBIGUOUS status refers to (`Entity`).
    entity: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> BusinessResult[T]:
        if (self.status is BusinessStatus.SUCCESS) != (self.data is not None):
            raise ValueError("data is present exactly when status is SUCCESS")
        if self.status is BusinessStatus.SUCCESS and self.source is None:
            raise ValueError("a SUCCESS result names its source")
        if self.candidates and self.status is not BusinessStatus.AMBIGUOUS:
            raise ValueError("candidates belong to AMBIGUOUS results only")
        if self.entity is not None and self.status not in (
            BusinessStatus.NOT_FOUND,
            BusinessStatus.AMBIGUOUS,
        ):
            raise ValueError("entity belongs to NOT_FOUND / AMBIGUOUS results only")
        return self
