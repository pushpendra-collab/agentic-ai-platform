"""The ERP access contract (Phase 14, ADR-063): what Q1 must supply, as a typed document.

The contract is a reviewed, non-secret JSON file (`ERP_CONTRACT_PATH`), kept with the
deployment configuration. It records what the ERP owner confirmed: who owns the ERP, which
database is the intended one, the read-only role, how tenants and warehouses are
isolated, the time-zone rules, the business rules and invoice statuses, and, for each of
the platform capabilities, exactly which ERP relations and columns answer it.

Nothing in this module connects to anything, and nothing here is ERP knowledge: the
contract is supplied by the owner. The module only checks that a contract is complete,
consistent and safe:

* `missing_inputs(contract)` lists every Q1 item (A-T) that is absent. With no contract
  every item is missing, which is the state of this repository today.
* `validate_contract(contract)` refuses inconsistencies: unknown capabilities, a mapping
  that would change a capability's permission, identifiers that are not plain SQL
  identifiers (no SQL fragments, ever), columns that serve no field of the platform's
  record contract (data minimization), rules a mapping relies on that the owner has not
  confirmed, an invalid time zone, and anything that looks like a credential.
* `ErpContract.status` is `DRAFT` until every item is present and the owner confirmed
  it; only a `VERIFIED` contract with a `VERIFIED` capability mapping may ever let the
  live adapter answer that capability (adapters/postgres.py).

Credentials are never part of the contract: they come from the secret store as
`ERP_DATABASE_URL` (OPERATIONS_RUNBOOK § 19).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agentic_platform.business.capabilities import CAPABILITIES
from agentic_platform.business.models import (
    CustomerCountRecord,
    CustomerRecord,
    CustomersBilledRecord,
    InvoiceRecord,
    LowStockRecord,
    OutstandingRecord,
    ProductRecord,
    SalesRecord,
    StockRecord,
    WarehouseRecord,
)
from agentic_platform.business.rules import BusinessRule
from agentic_platform.core.redaction import is_sensitive_key, redact_text

# A plain SQL identifier: what a contract may name. Never a fragment, never quoted text.
IDENTIFIER = r"^[a-z_][a-z0-9_]{0,62}$"
_IDENTIFIER = re.compile(IDENTIFIER)
CAPABILITY_NAMES = frozenset(c.name for c in CAPABILITIES)
CAPABILITY_PERMISSIONS = {c.name: c.permission for c in CAPABILITIES}


class Verification(StrEnum):
    DRAFT = "DRAFT"  # incomplete or not confirmed by the ERP owner
    VERIFIED = "VERIFIED"  # complete, confirmed by the owner, checked against the catalog


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Confirmation(_Strict):
    """Who confirmed something, when, and where that confirmation is recorded."""

    confirmed_by: str = Field(min_length=1, max_length=200)
    confirmed_on: date
    reference: str = Field(min_length=1, max_length=200)  # ticket / document id


# --- Q1 A, G: ownership ------------------------------------------------------------------


class Ownership(_Strict):
    erp_owner: str = Field(min_length=1, max_length=200)  # A
    application_owner: str = Field(min_length=1, max_length=200)
    is_intended_erp: bool  # G: the owner states this database IS the ERP to use
    confirmation: Confirmation


# --- Q1 B, C, D, T: database identity -----------------------------------------------------


class DatabaseIdentity(_Strict):
    host: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9.\-]+$")  # B
    port: int = Field(ge=1, le=65535)
    name: str = Field(pattern=IDENTIFIER)  # C (== ERP_EXPECTED_DATABASE)
    postgres_major_version: int = Field(ge=12, le=40)  # D
    schema_version: str = Field(min_length=1, max_length=64)  # T (e.g. Flyway version)


# --- Q1 E, F: access ----------------------------------------------------------------------


class Access(_Strict):
    read_only_role: str = Field(pattern=IDENTIFIER)  # E
    # F: how the credential reaches the platform (never a file in the repository). The
    # key is not called "credential...", which the credential scan would refuse.
    delivery: Literal["secret_store"]
    tls_mode: Literal["require", "verify-ca", "verify-full"]


# --- Q1 H, I: isolation -------------------------------------------------------------------


class TenantIsolation(_Strict):
    """H. How ERP rows belong to a platform tenant (exactly one mechanism)."""

    mechanism: Literal["column", "rls_session_setting"]
    # mechanism=column: every tenant-scoped relation has this column; reads filter on it
    # with the principal's tenant, mapped by `tenant_value`.
    column: str | None = Field(default=None, pattern=IDENTIFIER)
    # mechanism=rls_session_setting: the ERP's RLS policies read this setting; the adapter
    # sets it per read transaction to the principal's mapped tenant value.
    setting: str | None = Field(default=None, pattern=r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")
    # Optional: the policies call this function (e.g. InvenLogic's
    # `invenlogic_company_id()`) and the function reads `setting`. The gate then checks
    # the policies for the function and the function's body for the setting.
    policy_function: str | None = Field(default=None, pattern=IDENTIFIER)
    # How the platform tenant id becomes the ERP's tenant value.
    tenant_value: Literal["platform_tenant_id"]
    confirmation: Confirmation


class WarehouseIsolation(_Strict):
    """I. How stock rows belong to a warehouse/outlet, and how the platform's warehouse
    scope (business/context.py) maps onto them."""

    mechanism: Literal["column"]
    column: str = Field(pattern=IDENTIFIER)
    # The platform warehouse ref equals this ERP column of the warehouse relation.
    ref_column: str = Field(pattern=IDENTIFIER)
    # What a warehouse outside the caller's scope looks like. The existing security model
    # (business/results.py, ADR-059) hides it: NOT_FOUND, existence never revealed.
    out_of_scope: Literal["not_found"]
    confirmation: Confirmation


# --- Q1 J, P: time ------------------------------------------------------------------------


class TimeRules(_Strict):
    erp_timestamp_storage: Literal["utc", "timestamptz", "local_naive"]  # J
    business_timezone: str  # J (== ERP_BUSINESS_TIMEZONE); never assumed
    day_boundary: Literal["midnight_local"]  # P
    week_start: Literal["monday", "sunday"]  # P
    month: Literal["calendar"]  # P
    confirmation: Confirmation

    @field_validator("business_timezone")
    @classmethod
    def _iana(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"not an IANA time zone: {value!r}") from exc
        return value


# --- Q1 K-O, Q: business rules and statuses ------------------------------------------------


class RuleDefinition(_Strict):
    """One business rule in the owner's words, plus who confirmed it."""

    definition: str = Field(min_length=1, max_length=2000)
    confirmation: Confirmation


REQUIRED_RULES: dict[str, str] = {
    # rule id → Q1 letter and what it decides
    "business_ownership": "K: which tenant/company owns a document",
    "customer_identification": "L: how a customer is identified and matched by name",
    "sales_calculation": "M: posted/cancelled/returned sales, tax-inclusive or exclusive",
    "outstanding_balance": "N: outstanding, paid, partially paid, cancelled, disputed",
    "stock_calculation": "O: available/reserved/damaged/transferable stock",
    "date_period": "P: how dates and periods are interpreted",
    "invoice_status_semantics": "Q: what each invoice status means",
}


class InvoiceStatuses(_Strict):
    """Q. Every ERP invoice status value, grouped by meaning. A value not listed is an
    unexpected status: the read is refused as malformed, never guessed."""

    open: tuple[str, ...] = Field(min_length=1)
    partially_paid: tuple[str, ...] = ()
    paid: tuple[str, ...] = ()
    overdue: tuple[str, ...] = ()  # only if the ERP stores overdue as a status
    cancelled: tuple[str, ...] = ()
    disputed: tuple[str, ...] = ()
    draft: tuple[str, ...] = ()

    def all_values(self) -> set[str]:
        return {v for group in self.model_dump().values() for v in group}

    def counts_as_open(self) -> set[str]:
        return {*self.open, *self.partially_paid, *self.overdue}

    def group_of(self, value: str) -> str:
        """The meaning of one ERP status value. An unlisted value raises
        `UnexpectedStatusError`: a reader reports the row as malformed, never guesses."""
        for group, values in self.model_dump().items():
            if value in values:
                return str(group)
        raise UnexpectedStatusError("invoice status not in the ERP contract")


class UnexpectedStatusError(ValueError):
    """An ERP value outside the contract's closed list (the read is MALFORMED_RESPONSE)."""


# --- Q1 R: authorization expectations ------------------------------------------------------


class AuthorizationExpectations(_Strict):
    """R. What the ERP owner expects of the platform's authorization. The platform's
    permissions stay authoritative; this only records the agreement."""

    platform_permissions_authoritative: Literal[True]
    notes: str = Field(default="", max_length=2000)
    confirmation: Confirmation


# --- Q1 S + § 6: capability mappings -------------------------------------------------------


class RelationRef(_Strict):
    schema_name: str = Field(pattern=IDENTIFIER)
    name: str = Field(pattern=IDENTIFIER)
    kind: Literal["table", "view"]
    # Multi-database ERPs (`ErpContract.databases`): the key of the database holding the
    # relation. None for a single-database contract (`ErpContract.database`).
    database: str | None = Field(default=None, pattern=IDENTIFIER)

    @property
    def qualified(self) -> str:
        return f"{self.schema_name}.{self.name}"


# "match": searched to find an entity by name (e.g. a customer's short name), never returned.
ColumnRole = Literal["field", "tenant", "warehouse", "status", "date", "join", "match"]


class ColumnRef(_Strict):
    relation: str = Field(pattern=r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")  # schema.name
    column: str = Field(pattern=IDENTIFIER)
    # As `format_type` prints it: `text`, `numeric(14,2)`, `timestamp with time zone`.
    data_type: str = Field(pattern=r"^[a-z][a-z0-9 _]{0,40}(\([0-9]{1,4}(,[0-9]{1,4})?\))?$")
    role: ColumnRole
    # role=field: the platform record field this column fills (data minimization: a
    # column that fills no field and filters nothing is refused).
    record_field: str | None = None


class CapabilityMapping(_Strict):
    """How one platform capability is answered by the ERP (brief § 6, items 1-15)."""

    capability: str  # 1
    sources: tuple[RelationRef, ...] = Field(min_length=1)  # 2
    columns: tuple[ColumnRef, ...] = Field(min_length=1)  # 3
    joins: tuple[str, ...] = ()  # 4: described, never SQL
    tenant_boundary: str = Field(min_length=1, max_length=500)  # 5
    warehouse_boundary: str = Field(min_length=1, max_length=500)  # 6
    status_filter: tuple[str, ...] = ()  # 7: invoice status values that apply
    date_filter: str = Field(min_length=1, max_length=500)  # 8
    timezone_behavior: str = Field(min_length=1, max_length=500)  # 9
    null_behavior: str = Field(min_length=1, max_length=500)  # 10
    ambiguity_behavior: str = Field(min_length=1, max_length=500)  # 11
    not_found_behavior: str = Field(min_length=1, max_length=500)  # 12
    unavailable_behavior: str = Field(min_length=1, max_length=500)  # 13
    rules: tuple[str, ...] = ()  # 14: rule ids this answer depends on
    permission: str  # 15: must equal the capability's platform permission
    status: Verification = Verification.DRAFT
    confirmation: Confirmation | None = None


# Which platform record fields each capability's mapping may fill (data minimization).
CAPABILITY_RECORDS: dict[str, tuple[type[BaseModel], ...]] = {
    "customer_outstanding": (CustomerRecord, OutstandingRecord),
    "customer_open_invoices": (CustomerRecord, InvoiceRecord),
    "daily_sales": (SalesRecord,),
    "product_stock": (ProductRecord, WarehouseRecord, StockRecord),
    "warehouse_stock": (ProductRecord, WarehouseRecord, StockRecord),
    "low_stock_products": (ProductRecord, WarehouseRecord, LowStockRecord),
    "customer_count": (CustomerCountRecord, CustomersBilledRecord),
}


def allowed_fields(capability: str) -> set[str]:
    records = CAPABILITY_RECORDS.get(capability, ())
    return {name for record in records for name in record.model_fields} - {"tenant_id"}


# --- the contract --------------------------------------------------------------------------


ReaderSet = Literal["invenlogic_v1"]


class ErpContract(_Strict):
    contract_version: str = Field(min_length=1, max_length=32)
    status: Verification = Verification.DRAFT
    ownership: Ownership | None = None  # A, G
    database: DatabaseIdentity | None = None  # B, C, D, T
    # B, C, D, T for an ERP split over several databases of one owner (InvenLogic: one
    # database per service). Key → identity; every relation names its key. Exclusive
    # with `database`.
    databases: dict[str, DatabaseIdentity] = Field(default_factory=dict)
    # Which reviewed reader code answers the mapped capabilities (adapters/invenlogic.py).
    # None: no reader is registered, every capability stays RULE_NOT_VERIFIED.
    reader_set: ReaderSet | None = None
    # The currency of every amount the ERP stores (the ERP keeps no currency per document).
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    access: Access | None = None  # E, F
    tenant_isolation: TenantIsolation | None = None  # H
    warehouse_isolation: WarehouseIsolation | None = None  # I
    time: TimeRules | None = None  # J, P
    rules: dict[str, RuleDefinition] = Field(default_factory=dict)  # K-O, Q
    invoice_statuses: InvoiceStatuses | None = None  # Q
    # R (not named "authorization...": the credential scan refuses such keys)
    permission_expectations: AuthorizationExpectations | None = None
    capabilities: dict[str, CapabilityMapping] = Field(default_factory=dict)  # S

    def mapping(self, capability: str) -> CapabilityMapping | None:
        return self.capabilities.get(capability)

    def capability_verified(self, capability: str) -> bool:
        mapping = self.mapping(capability)
        return (
            self.status is Verification.VERIFIED
            and mapping is not None
            and mapping.status is Verification.VERIFIED
        )

    def verified_business_rules(self) -> frozenset[str]:
        """Platform `BusinessRule`s the owner confirmed (the live adapter's verified_rules)."""
        if self.status is not Verification.VERIFIED:
            return frozenset()
        known = {rule.value for rule in BusinessRule}
        return frozenset(rule for rule in self.rules if rule in known)

    def relations(self, database: str | None = None) -> list[RelationRef]:
        """Every mapped relation; with `database`, only those of that database key."""
        seen: dict[tuple[str | None, str], RelationRef] = {}
        for mapping in self.capabilities.values():
            for relation in mapping.sources:
                if database is None or relation.database == database:
                    seen.setdefault((relation.database, relation.qualified), relation)
        return list(seen.values())

    def database_identity(self, key: str | None) -> DatabaseIdentity | None:
        """The identity of database `key` (None: the single-database contract's)."""
        return self.database if key is None else self.databases.get(key)


# --- Q1 checklist --------------------------------------------------------------------------


class MissingInput(_Strict):
    item: str  # Q1 letter
    description: str


Q1_ITEMS: dict[str, str] = {
    "A": "ERP owner confirmation",
    "B": "ERP database host",
    "C": "ERP database name",
    "D": "ERP PostgreSQL version",
    "E": "approved read-only database role",
    "F": "read-only credentials supplied through the secret store",
    "G": "confirmation that the database is actually the intended ERP",
    "H": "tenant isolation mechanism",
    "I": "warehouse/outlet isolation mechanism",
    "J": "time zone rules",
    "K": "business ownership rules",
    "L": "customer identification rules",
    "M": "sales calculation rules",
    "N": "outstanding-balance rules",
    "O": "stock calculation rules",
    "P": "date/period rules",
    "Q": "invoice status semantics",
    "R": "authorization expectations",
    "S": "views/functions or tables per capability (capability mappings)",
    "T": "schema/version information",
}


def missing_inputs(contract: ErpContract | None) -> list[MissingInput]:
    """Every Q1 item the contract does not supply (all of them without a contract)."""
    present: set[str] = set()
    if contract is not None:
        c = contract
        if c.ownership is not None:
            present.add("A")
            if c.ownership.is_intended_erp:
                present.add("G")
        if c.database is not None or c.databases:
            present |= {"B", "C", "D", "T"}
        if c.access is not None:
            present |= {"E", "F"}
        if c.tenant_isolation is not None:
            present.add("H")
        if c.warehouse_isolation is not None:
            present.add("I")
        if c.time is not None:
            present.add("J")
        letters = {rule: text.split(":")[0] for rule, text in REQUIRED_RULES.items()}
        present |= {letters[rule] for rule in c.rules if rule in letters}
        if c.time is None:
            present.discard("P")
        if c.invoice_statuses is None:
            present.discard("Q")
        if c.permission_expectations is not None:
            present.add("R")
        if set(c.capabilities) >= CAPABILITY_NAMES:
            present.add("S")
    return [MissingInput(item=k, description=v) for k, v in Q1_ITEMS.items() if k not in present]


# --- validation ----------------------------------------------------------------------------


def _credential_like(value: Any, path: str = "") -> Iterable[str]:
    if isinstance(value, dict):
        for key, inner in value.items():
            where = f"{path}.{key}" if path else str(key)
            if isinstance(key, str) and is_sensitive_key(key):
                yield f"{where}: a contract must not contain credentials"
            yield from _credential_like(inner, where)
    elif isinstance(value, list | tuple):
        for index, inner in enumerate(value):
            yield from _credential_like(inner, f"{path}[{index}]")
    elif isinstance(value, str) and (redact_text(value) != value or "://" in value):
        yield f"{path}: looks like a credential or connection string"


def validate_contract(contract: ErpContract) -> list[str]:
    """Problems that make the contract unusable (empty = consistent)."""
    problems: list[str] = list(_credential_like(contract.model_dump(mode="json")))
    for key, mapping in contract.capabilities.items():
        where = f"capabilities.{key}"
        if key != mapping.capability:
            problems.append(f"{where}: key and capability differ")
        if mapping.capability not in CAPABILITY_NAMES:
            problems.append(f"{where}: unknown capability (the platform capabilities only)")
            continue
        if mapping.permission != CAPABILITY_PERMISSIONS[mapping.capability]:
            problems.append(f"{where}: permission would change the public capability contract")
        sources = {r.qualified for r in mapping.sources}
        if len(sources) != len({(r.database, r.qualified) for r in mapping.sources}):
            problems.append(f"{where}: one relation name in two databases (ambiguous columns)")
        for relation in mapping.sources:
            if contract.databases and relation.database not in contract.databases:
                problems.append(f"{where}: {relation.qualified} names no contract database")
            if contract.database is not None and relation.database is not None:
                problems.append(f"{where}: {relation.qualified} names a database key in a "
                                "single-database contract")  # fmt: skip
            if contract.databases and relation.database is None:
                problems.append(f"{where}: {relation.qualified} names no database key")
        fields = allowed_fields(mapping.capability)
        for column in mapping.columns:
            if column.relation not in sources:
                problems.append(f"{where}: column {column.column} of an unlisted relation")
            if column.role == "field":
                if column.record_field not in fields:
                    problems.append(
                        f"{where}: column {column.column} fills no platform record field "
                        "(data minimization)"
                    )
            elif column.record_field is not None:
                problems.append(f"{where}: a {column.role} column fills no record field")
        roles = {column.role for column in mapping.columns}
        if (
            contract.tenant_isolation is not None
            and contract.tenant_isolation.mechanism == ("column")
            and "tenant" not in roles
        ):
            problems.append(f"{where}: no tenant column although tenants are column-isolated")
        stock = mapping.capability in ("product_stock", "warehouse_stock", "low_stock_products")
        if stock and "warehouse" not in roles:
            problems.append(f"{where}: stock is answered without a warehouse column")
        for rule in mapping.rules:
            if rule not in contract.rules:
                problems.append(f"{where}: relies on rule {rule!r}, which is not confirmed")
        if contract.invoice_statuses is not None:
            unknown = set(mapping.status_filter) - contract.invoice_statuses.all_values()
            if unknown:
                problems.append(f"{where}: status filter uses unlisted statuses {sorted(unknown)}")
        if mapping.status is Verification.VERIFIED and mapping.confirmation is None:
            problems.append(f"{where}: VERIFIED without the owner's confirmation")
    if contract.database is not None and contract.databases:
        problems.append("`database` and `databases` are exclusive")
    for key in contract.databases:
        if not _IDENTIFIER.match(key):
            problems.append(f"databases.{key}: not a plain identifier")
    isolation = contract.tenant_isolation
    if isolation is not None:
        if isolation.policy_function is not None and isolation.mechanism != (
            "rls_session_setting"
        ):
            problems.append("tenant_isolation: policy_function needs mechanism=rls_session_setting")
        if isolation.mechanism == "column" and isolation.column is None:
            problems.append("tenant_isolation: mechanism=column needs `column`")
        if isolation.mechanism == "rls_session_setting" and isolation.setting is None:
            problems.append("tenant_isolation: mechanism=rls_session_setting needs `setting`")
    if contract.status is Verification.VERIFIED:
        missing = missing_inputs(contract)
        if missing:
            items = ", ".join(m.item for m in missing)
            problems.append(f"status VERIFIED but Q1 items are missing: {items}")
        if contract.ownership is not None and not contract.ownership.is_intended_erp:
            problems.append("status VERIFIED but the owner did not confirm the intended ERP")
        for rule in REQUIRED_RULES:
            if rule not in contract.rules:
                problems.append(f"status VERIFIED but rule {rule!r} is not defined")
    if {BusinessRule.YEAR_IS_CALENDAR_YEAR, BusinessRule.YEAR_IS_FINANCIAL_YEAR_APRIL} <= set(
        contract.rules
    ):
        problems.append("rules: a year is either the calendar year or the financial year")
    if contract.reader_set is not None and contract.currency is None:
        problems.append("reader_set needs `currency` (the ERP stores none per document)")
    return problems


class ContractError(ValueError):
    """The contract file is missing, unreadable, invalid or inconsistent."""


def load_contract(path: str | Path) -> ErpContract:
    """Read and validate a contract file. Raises `ContractError` with safe messages."""
    file = Path(path)
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError(f"ERP contract file not found: {file.name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        name = type(exc).__name__
        raise ContractError(f"ERP contract file is not readable JSON: {name}") from exc
    try:
        contract = ErpContract.model_validate(raw)
    except ValidationError as exc:
        locations = sorted({".".join(map(str, e["loc"])) for e in exc.errors(include_input=False)})
        raise ContractError(f"ERP contract is invalid at: {', '.join(locations[:20])}") from None
    problems = validate_contract(contract)
    if problems:
        raise ContractError("ERP contract is inconsistent: " + "; ".join(problems[:20]))
    return contract


__all__ = [
    "CAPABILITY_NAMES",
    "IDENTIFIER",
    "Q1_ITEMS",
    "REQUIRED_RULES",
    "CapabilityMapping",
    "ColumnRef",
    "ContractError",
    "ErpContract",
    "InvoiceStatuses",
    "MissingInput",
    "RelationRef",
    "UnexpectedStatusError",
    "Verification",
    "allowed_fields",
    "load_contract",
    "missing_inputs",
    "validate_contract",
]
