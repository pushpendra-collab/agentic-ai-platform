"""Public request and response models of the HTTP API (version 1).

Requests reject unknown fields (`extra="forbid"`), so a body can never carry
`tenant_id`, `user_id`, roles or permissions: identity comes only from the bearer
token. Responses expose what a client needs, not internal graph state (no node
names, checkpoints, hashes, policies or tool arguments).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from agentic_platform.agents.schemas import AgentResponse, ResponseStatus, ThreadStatus
from agentic_platform.business.composition import format_amount, format_quantity
from agentic_platform.business.grounding import business_results
from agentic_platform.memory.models import MemoryRecord, ScoredMemory
from agentic_platform.security.identity import Principal
from agentic_platform.services.approvals import ApprovalRecord, ApprovalStatus
from agentic_platform.services.audit import AuditRecord

ID_PATTERN = r"^[A-Za-z0-9_.:\-]{1,128}$"
NAME_PATTERN = r"^[A-Za-z0-9_.:\-]{1,100}$"
MAX_MESSAGE_CHARS = 100_000  # the agent applies AGENT_MAX_INPUT_CHARS on top
MAX_DOCUMENT_CHARS = 500_000


class ApiRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- identity -----------------------------------------------------------------------


class PrincipalView(BaseModel):
    """What the caller may know about their own identity (no session secrets)."""

    user_id: str
    tenant_id: str
    roles: list[str]
    permissions: list[str]
    auth_method: str
    authenticated_at: datetime

    @classmethod
    def of(cls, principal: Principal) -> PrincipalView:
        return cls(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            roles=sorted(principal.roles),
            permissions=sorted(principal.permissions),
            auth_method=principal.auth_method,
            authenticated_at=principal.authenticated_at,
        )


# --- agent turns --------------------------------------------------------------------


class TurnRequest(ApiRequest):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    # Continue this conversation, or start one with this id. Omit to start a new one.
    thread_id: str | None = Field(default=None, pattern=ID_PATTERN)
    # Repeat-safe submission: the same key on the same conversation returns the
    # stored response instead of running the turn again (needs a database).
    idempotency_key: str | None = Field(default=None, pattern=ID_PATTERN)


class MessageRequest(ApiRequest):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    idempotency_key: str | None = Field(default=None, pattern=ID_PATTERN)


class StartConversationRequest(ApiRequest):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ResumeRequest(ApiRequest):
    # The answer to a question the paused turn asked. Omit when resuming after an
    # approval decision (the decision is read from the approval record, never from here).
    answer: JsonValue | None = None


class ApprovalRequired(BaseModel):
    approval_id: str
    operation: str
    summary: str
    risk: str
    expires_at: str | None = None


class ToolCallView(BaseModel):
    tool_name: str
    status: str
    error_code: str | None = None
    approval_id: str | None = None


class TableView(BaseModel):
    """Rows of a business list, for clients that show tables. Built only from the
    tool's own SUCCESS result (same figures as the reply, no derived values)."""

    title: str
    columns: list[str]
    rows: list[list[str]]
    truncated: bool = False


def _name(ref: Any) -> str:
    return str(ref.get("display_name", "")) if isinstance(ref, dict) else ""


def _qty(value: Any, unit: Any) -> str:
    return format_quantity(str(value), unit or None)


def _amount(money: Any) -> str:
    if not isinstance(money, dict):
        return ""
    return format_amount(str(money.get("amount", "")), money.get("currency"))


def _rows(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [i for i in data.get(key, []) if isinstance(i, dict)]


def _low_stock_table(data: dict[str, Any], _: bool) -> TableView | None:
    items = _rows(data, "items")
    if not items:
        return None
    return TableView(
        title="Low-stock products",
        columns=["Product", "Warehouse", "Stock", "Low-stock level"],
        rows=[
            [
                _name(i.get("product")),
                _name(i.get("warehouse")),
                _qty(i.get("quantity", ""), i.get("unit")),
                _qty(i.get("threshold", ""), i.get("unit")),
            ]
            for i in items
        ],
        truncated=bool(data.get("truncated")),
    )


def _invoices_table(data: dict[str, Any], overdue_only: bool) -> TableView | None:
    lines = _rows(data, "invoices")
    # "Overdue" only when the service decided it (rule verified, list complete).
    if overdue_only and data.get("overdue_count") is not None:
        lines = [i for i in lines if i.get("overdue") is True]
    if not lines:
        return None
    customer = _name(data.get("customer"))
    with_customer = not customer
    columns = ["Bill no.", "Date", "Due date", "Balance", "Overdue"]
    if with_customer:
        columns.insert(1, "Customer")

    def row(i: dict[str, Any]) -> list[str]:
        overdue = {True: "Yes", False: "No"}.get(i.get("overdue"), "-")
        cells = [
            _name(i.get("invoice")),
            str(i.get("issued_on") or "-"),
            str(i.get("due_on") or "-"),
            _amount(i.get("balance")),
            overdue,
        ]
        if with_customer:
            cells.insert(1, _name(i.get("customer")))
        return cells

    what = "Overdue bills" if overdue_only else "Pending bills"
    return TableView(
        title=f"{what} of {customer}" if customer else what,
        columns=columns,
        rows=[row(i) for i in lines],
        truncated=bool(data.get("truncated")),
    )


def _warehouse_table(data: dict[str, Any], _: bool) -> TableView | None:
    lines = _rows(data, "lines")
    if not lines:
        return None
    return TableView(
        title=f"Stock in {_name(data.get('warehouse'))}",
        columns=["Product", "Stock"],
        rows=[[_name(i.get("product")), _qty(i.get("quantity", ""), i.get("unit"))] for i in lines],
        truncated=bool(data.get("truncated")),
    )


def _product_table(data: dict[str, Any], _: bool) -> TableView | None:
    lines = _rows(data, "lines")
    if len(lines) < 2:  # one warehouse: the sentence says it all
        return None
    unit = data.get("unit")
    return TableView(
        title=f"Stock of {_name(data.get('product'))}",
        columns=["Warehouse", "Stock"],
        rows=[[_name(i.get("warehouse")), _qty(i.get("quantity", ""), unit)] for i in lines]
        + [["Total", _qty(data.get("total_quantity", ""), unit)]],
    )


_TABLES = {
    "low_stock_products": _low_stock_table,
    "customer_open_invoices": _invoices_table,
    "warehouse_stock": _warehouse_table,
    "product_stock": _product_table,
}


def _tables_of(response: AgentResponse) -> list[TableView]:
    """Tables from the turn's SUCCESS business results only; the figures are the tool's
    own (formatting only, the service's total included, nothing computed here)."""
    overdue_only = "overdue" in (response.intent or "").casefold() or (
        "Overdue invoices:" in response.message
    )
    tables: list[TableView] = []
    for out in business_results(response.tool_results):
        build = _TABLES.get(str(out.get("capability")))
        data = out.get("data")
        if build is None or out.get("status") != "SUCCESS" or not isinstance(data, dict):
            continue
        table = build(data, overdue_only)
        if table is not None:
            tables.append(table)
    return tables


class ErrorInfo(BaseModel):
    code: str
    message: str
    retryable: bool = False


def _approval_of(value: Any) -> ApprovalRequired | None:
    if isinstance(value, dict) and value.get("type") == "approval_required":
        return ApprovalRequired(
            approval_id=str(value.get("approval_id", "")),
            operation=str(value.get("operation", "")),
            summary=str(value.get("summary", "")),
            risk=str(value.get("risk", "")),
            expires_at=str(value["expires_at"]) if value.get("expires_at") else None,
        )
    return None


class TurnResponse(BaseModel):
    """One agent turn. `status`:

    * `completed`, `needs_clarification`, `unsupported`: finished (HTTP 200);
    * `interrupted`: paused; `approval_required` says what waits for approval, or
      `input_request` holds the question the turn asked (HTTP 202);
    * `failed`: `error` says why; `retryable` errors may be retried (HTTP 409/5xx).
    """

    request_id: str
    thread_id: str
    status: ResponseStatus
    message: str
    approval_required: ApprovalRequired | None = None
    input_request: JsonValue | None = None
    tool_calls: list[ToolCallView] = Field(default_factory=list)
    # Business lists as rows and columns (currently: low-stock products).
    tables: list[TableView] = Field(default_factory=list)
    error: ErrorInfo | None = None
    replayed: bool = False
    duration_ms: int = 0

    @classmethod
    def of(cls, response: AgentResponse) -> TurnResponse:
        approval, question = None, None
        for interrupt in response.interrupts:
            approval = approval or _approval_of(interrupt.value)
            if _approval_of(interrupt.value) is None:
                question = interrupt.value
        error = response.errors[0] if response.errors else None
        return cls(
            request_id=response.request_id,
            thread_id=response.thread_id,
            status=response.status,
            message=response.message,
            approval_required=approval,
            input_request=question,
            tool_calls=[
                ToolCallView(
                    tool_name=r.tool_name,
                    status=r.status,
                    error_code=r.error_code,
                    approval_id=r.approval_id,
                )
                for r in response.tool_results
            ],
            tables=_tables_of(response) if response.status == "completed" else [],
            error=ErrorInfo(code=error.code, message=error.message, retryable=error.retryable)
            if error and response.status == "failed"
            else None,
            replayed=response.replayed,
            duration_ms=response.duration_ms,
        )


# --- conversations ------------------------------------------------------------------


class ConversationRefView(BaseModel):
    thread_id: str


class ConversationListView(BaseModel):
    conversations: list[ConversationRefView]


class ConversationView(BaseModel):
    thread_id: str
    # A turn started but did not finish: paused (approval or question) or crashed.
    pending: bool
    awaiting_approval: ApprovalRequired | None = None
    awaiting_input: bool = False
    message_count: int
    last_request_id: str | None = None
    updated_at: str | None = None

    @classmethod
    def of(cls, status: ThreadStatus) -> ConversationView:
        approval = next(
            (a for i in status.interrupts if (a := _approval_of(i.value)) is not None), None
        )
        return cls(
            thread_id=status.thread_id,
            pending=status.pending,
            awaiting_approval=approval,
            awaiting_input=status.interrupted and approval is None,
            message_count=status.message_count,
            last_request_id=status.last_request_id,
            updated_at=status.updated_at,
        )


class TurnView(BaseModel):
    role: str
    content: str


class HistoryView(BaseModel):
    thread_id: str
    turns: list[TurnView]


class ForgetView(BaseModel):
    thread_id: str
    deleted: dict[str, int]


# --- approvals ----------------------------------------------------------------------


class ApprovalView(BaseModel):
    approval_id: str
    operation: str
    summary: str
    risk: str
    status: ApprovalStatus
    requested_by: str
    created_at: datetime
    expires_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    # Only shown to the requester (it names their private conversation).
    thread_id: str | None = None
    # May the caller approve or reject it (permission, self-approval policy, still open)?
    can_decide: bool = False

    @classmethod
    def of(cls, record: ApprovalRecord, viewer: Principal) -> ApprovalView:
        lapsed = record.expired and record.status in ("pending", "approved")
        mine = record.requester_id == viewer.user_id
        return cls(
            approval_id=str(record.id),
            operation=record.operation,
            summary=record.summary,
            risk=record.risk,
            status="expired" if lapsed else record.status,
            requested_by=record.requester_id,
            created_at=record.created_at,
            expires_at=record.expires_at,
            decided_by=record.decided_by,
            decided_at=record.decided_at,
            thread_id=record.thread_id if mine else None,
            can_decide=record.status == "pending"
            and not lapsed
            and viewer.has(record.approver_permission)
            and (not mine or record.allow_self_approval),
        )


class ApprovalListView(BaseModel):
    approvals: list[ApprovalView]


class DecisionRequest(ApiRequest):
    note: str | None = Field(default=None, max_length=1000)
    # Requester deciding their own request (where allowed): resume the paused turn now.
    resume: bool = False


class DecisionView(BaseModel):
    approval: ApprovalView
    # none · resume_conversation (the requester resumes their conversation) · resumed
    next_action: Literal["none", "resume_conversation", "resumed"]
    turn: TurnResponse | None = None
    resume_error: ErrorInfo | None = None


# --- memory -------------------------------------------------------------------------


class FactWriteRequest(ApiRequest):
    content: str = Field(min_length=1, max_length=MAX_DOCUMENT_CHARS)
    # Tenant-wide fact (needs memory.shared.write) instead of a private one.
    shared: bool = False
    # Optional expiry chosen by the writer (the existing long-term-memory TTL option).
    ttl_seconds: int | None = Field(default=None, ge=60, le=10 * 365 * 24 * 3600)


class FactView(BaseModel):
    id: str
    namespace: str
    key: str | None
    content: str
    shared: bool
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None

    @classmethod
    def of(cls, record: MemoryRecord) -> FactView:
        return cls(
            id=str(record.id),
            namespace=record.namespace,
            key=record.key,
            content=record.content,
            shared=not record.user_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
            expires_at=record.expires_at,
        )


class FactListView(BaseModel):
    facts: list[FactView]


class DocumentWriteRequest(ApiRequest):
    text: str = Field(min_length=1, max_length=MAX_DOCUMENT_CHARS)
    title: str | None = Field(default=None, max_length=300)
    # Tenant knowledge (default; needs memory.shared.write) or a private note.
    shared: bool = True
    source_type: Literal["document", "policy", "procedure", "faq", "reference", "note"] = (
        "document"
    )


class DocumentWriteView(BaseModel):
    source_ref: str
    chunks: int
    shared: bool


class KnowledgeSearchRequest(ApiRequest):
    query: str = Field(min_length=1, max_length=4000)
    k: int = Field(default=5, ge=1, le=20)


class KnowledgeHit(BaseModel):
    content: str
    score: float
    source_ref: str | None = None
    title: str | None = None

    @classmethod
    def of(cls, hit: ScoredMemory) -> KnowledgeHit:
        title = hit.record.metadata.get("title")
        return cls(
            content=hit.record.content,
            score=round(hit.score, 4),
            source_ref=hit.record.source_ref,
            title=str(title) if title else None,
        )


class KnowledgeSearchView(BaseModel):
    hits: list[KnowledgeHit]


class DeletedView(BaseModel):
    deleted: int


# --- audit --------------------------------------------------------------------------


class AuditEntryView(BaseModel):
    id: int
    occurred_at: str
    action: str
    status: str
    actor_type: str
    actor_id: str | None = None
    tool_name: str | None = None
    error_code: str | None = None
    request_id: str | None = None
    thread_id: str | None = None
    entity_refs: list[dict[str, str]] = Field(default_factory=list)
    approval_decision: str | None = None
    approver_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def of(cls, record: AuditRecord) -> AuditEntryView:
        return cls.model_validate(record.model_dump(exclude={"tenant_id", "input_hash"}))


class AuditListView(BaseModel):
    entries: list[AuditEntryView]


# --- health -------------------------------------------------------------------------


class HealthView(BaseModel):
    status: Literal["ok"]
