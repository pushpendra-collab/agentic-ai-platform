"""Read-only PostgreSQL boundary to the ERP (Phase 10, ADR-059).

Reuses the platform's database layer (`Database`, `UnitOfWork(read_only=True)`,
`translate_db_error`) with its own engine and pool; nothing here touches the Agent DB,
Alembic or `create_all`. Safety layers, outermost first:

1. **Database role** (ERP DBA): a read-only role (`scripts/inspect_erp_security.py`,
   OPERATIONS_RUNBOOK § 15). Not something this code can grant itself.
2. **Connection**: every session starts with `default_transaction_read_only=on`, a
   server `statement_timeout` (never unlimited), a client `command_timeout`, its own
   `application_name`; and before the first read the connection proves it is the
   expected ERP database (name), not the Agent DB (no `alembic_version`), read only,
   and not a superuser / BYPASSRLS role. Otherwise every read fails closed.
3. **Transaction**: each read is one `SET TRANSACTION READ ONLY` unit, always rolled back.
4. **Statement**: only `ErpQuery` constants declared in code run, and each passes
   `check_read_only_sql` (a single SELECT/WITH statement, no comments, no write,
   DDL, locking or side-effect keywords or functions). There is no API that executes
   caller-supplied text; tools and the LLM never reach this module.
5. **Rows**: at most `max_rows + 1` rows are fetched per statement.

`PostgresErpAdapter` has **no schema mapping**: no ERP table or column is known (Q1), so
every capability raises `ErpRuleNotVerifiedError` (UNAVAILABLE / RULE_NOT_VERIFIED)
without connecting. A later phase adds one verified `ErpQuery` + row mapping per
capability, after the ERP owner confirmed the tables, columns and rules
(docs/SCHEMA_INTEGRATION.md).

Phase 14 (ADR-063) adds, without any ERP knowledge:

* **the security gate** (`business/erp_gate.py`): before the first read, the role's
  *effective* privileges are read from the catalog (no write privilege anywhere, no
  CREATE, owns nothing, no elevated membership, no CREATEDB/CREATEROLE/REPLICATION) and,
  with an ERP contract (`business/erp_contract.py`), the PostgreSQL version, every mapped
  relation and column with its type, and the confirmed tenant/warehouse isolation;
* **tenant binding on every business read**: `fetch` requires the principal's tenant.
  With `mechanism=column` the reserved `:tenant` parameter is always the principal's
  tenant (a caller value is overwritten, a query without `:tenant` is refused); with
  `mechanism=rls_session_setting` the confirmed setting is set for the read transaction;
* **capability readers**: the adapter answers a capability only through a reader
  registered for it, and a reader can be registered only when the contract is VERIFIED
  and that capability's mapping is VERIFIED. No reader exists in this repository.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Protocol
from urllib.parse import urlsplit

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Re-exported (Phase 14 moved the guard; existing imports from this module keep working).
from agentic_platform.business.adapters.sql_guard import ErpQuery as ErpQuery
from agentic_platform.business.adapters.sql_guard import (
    check_read_only_sql as check_read_only_sql,
)
from agentic_platform.business.context import BusinessContext
from agentic_platform.business.erp_contract import ErpContract
from agentic_platform.business.erp_gate import (
    FUNCTION_QUERY,
    POLICIES_QUERY,
    PRIVILEGE_PARAMS,
    PRIVILEGES_QUERY,
    SCHEMA_QUERY,
    evaluate_contract_schema,
    evaluate_privileges,
    gate_params,
)
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
from agentic_platform.business.ports import (
    ErpGateState,
    ErpRuleNotVerifiedError,
    ErpSafetyViolationError,
    ErpTimeoutError,
    ErpUnavailableError,
)
from agentic_platform.core.config import DatabaseSettings, ErpSettings
from agentic_platform.core.errors import (
    ConfigurationError,
    DatabaseError,
    DatabasePermissionError,
    DatabaseReadOnlyError,
    DatabaseTimeoutError,
)
from agentic_platform.core.logging import get_logger
from agentic_platform.db.engine import Database, engine_options

logger = get_logger(__name__)

APPLICATION_NAME = "agentic_erp_reader"

IDENTITY_QUERY = ErpQuery(
    "erp.identity",
    "SELECT current_database() AS database,"
    " current_setting('transaction_read_only') AS read_only,"
    " EXISTS (SELECT 1 FROM pg_catalog.pg_class WHERE relname = 'alembic_version'"
    "         AND relkind = 'r') AS has_alembic_version,"
    " r.rolsuper AS superuser, r.rolbypassrls AS bypass_rls"
    " FROM pg_catalog.pg_roles r WHERE r.rolname = current_user",
)


# --- connection (layers 2, 3, 5) --------------------------------------------------------


def erp_database_settings(
    settings: ErpSettings, url: SecretStr | None = None
) -> DatabaseSettings:
    """`DatabaseSettings` for the ERP pool. Every field is set explicitly, so no DATABASE_*
    variable (the Agent DB's) can leak into the ERP connection. `url`: one database of a
    multi-database ERP (`ERP_DATABASE_URLS`); default `ERP_DATABASE_URL`."""
    url = url or settings.database_url
    if url is None:
        raise ConfigurationError("ERP_DATABASE_URL is not set.")
    return DatabaseSettings(
        url=url,
        migration_url=None,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout_seconds=settings.pool_timeout_seconds,
        pool_recycle_seconds=1800,
        pool_pre_ping=True,
        connect_timeout_seconds=settings.connect_timeout_seconds,
        statement_timeout_ms=settings.statement_timeout_ms,
        command_timeout_seconds=settings.statement_timeout_ms / 1000 + 2,
        idle_in_transaction_timeout_ms=30_000,
        ssl_mode=settings.ssl_mode,
        application_name=APPLICATION_NAME,
        health_timeout_seconds=3.0,
        echo_sql=False,  # never log ERP statement parameters
    )


def read_only_engine(settings: DatabaseSettings) -> AsyncEngine:
    options = engine_options(settings)
    options["connect_args"]["server_settings"]["default_transaction_read_only"] = "on"
    return create_async_engine(settings.runtime_url(), **options)


def same_endpoint(url_a: str, url_b: str) -> bool:
    def key(url: str) -> tuple[str, int, str]:
        parts = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://", 1))
        return (parts.hostname or "").lower(), parts.port or 5432, parts.path.lstrip("/")

    return key(url_a) == key(url_b)


class ReadOnlyErpDatabase:
    def __init__(
        self,
        settings: ErpSettings,
        *,
        agent_database_urls: Sequence[str] = (),
        engine: AsyncEngine | None = None,
        contract: ErpContract | None = None,
        database_key: str | None = None,
        url: SecretStr | None = None,
    ) -> None:
        """`database_key`: one database of a multi-database contract (`databases`); its
        expected name comes from the contract and only its relations are gated."""
        db_settings = erp_database_settings(settings, url)
        self._key = database_key
        if database_key is not None:
            identity = contract.databases.get(database_key) if contract is not None else None
            if identity is None:
                raise ConfigurationError(f"ERP database {database_key!r} is not in the contract.")
            expected: str | None = identity.name
        else:
            expected = settings.expected_database
        if expected is None:
            raise ConfigurationError("ERP_EXPECTED_DATABASE is not set.")
        runtime_url = db_settings.runtime_url()
        if any(same_endpoint(runtime_url, other) for other in agent_database_urls if other):
            raise ConfigurationError("ERP_DATABASE_URL names the Agent DB.")
        self._expected = expected
        self._contract = contract
        if (
            database_key is None
            and contract is not None
            and contract.database is not None
            and (contract.database.name != settings.expected_database)
        ):
            raise ConfigurationError("ERP_EXPECTED_DATABASE differs from the ERP contract.")
        self._max_rows = settings.max_rows
        self._db = Database(db_settings, engine=engine or read_only_engine(db_settings))
        self._verified = False
        self._refused = False

    @property
    def key(self) -> str | None:
        """The contract's key of this database (None: single-database contract)."""
        return self._key

    @property
    def verified(self) -> bool:
        return self._verified

    @property
    def refused(self) -> bool:
        """Phase 15: the security gate refused this connection (reported by readiness)."""
        return self._refused

    async def verify(self) -> None:
        """Prove, once, that this is the expected read-only ERP database (layer 2)."""
        if self._verified:
            return
        rows = await self._execute(IDENTITY_QUERY, {}, max_rows=1)
        if not rows:
            raise ErpSafetyViolationError("ERP identity check returned no role row.")
        row = rows[0]
        problems = [
            message
            for failed, message in (
                (row["database"] != self._expected, "unexpected database name"),
                (bool(row["has_alembic_version"]), "this is the Agent DB (alembic_version)"),
                (row["read_only"] != "on", "session is not read only"),
                (bool(row["superuser"]), "role is a superuser"),
                (bool(row["bypass_rls"]), "role has BYPASSRLS"),
            )
            if failed
        ]
        # Phase 14: effective privileges, and the contract's schema and isolation.
        privileges = await self._execute(PRIVILEGES_QUERY, PRIVILEGE_PARAMS, max_rows=1)
        privilege_row = privileges[0] if privileges else None
        problems += evaluate_privileges(privilege_row)
        if self._contract is not None and privilege_row is not None:
            params = gate_params(self._contract, self._key)
            schema = await self._execute(SCHEMA_QUERY, params["schema"], max_rows=5000)
            policies = await self._execute(POLICIES_QUERY, params["policies"], max_rows=5000)
            isolation = self._contract.tenant_isolation
            functions = (
                await self._execute(FUNCTION_QUERY, params["function"], max_rows=1)
                if isolation is not None and isolation.policy_function is not None
                else []
            )
            problems += evaluate_contract_schema(
                self._contract, privilege_row, schema, policies,
                database=self._key, function_rows=functions,
            )  # fmt: skip
        if problems:
            self._refused = True
            logger.error("erp.identity.refused", extra={"fields": {"problems": problems}})
            raise ErpSafetyViolationError("ERP connection refused: " + "; ".join(problems))
        self._refused = False
        self._verified = True
        logger.info("erp.identity.verified", extra={"fields": {"database": self._expected}})

    async def fetch(
        self,
        query: ErpQuery,
        params: Mapping[str, Any] | None = None,
        *,
        max_rows: int | None = None,
        tenant_id: str,
    ) -> list[dict[str, Any]]:
        """Run one reviewed business read for `tenant_id` (the principal's tenant, from the
        `BusinessContext`) and return at most `max_rows + 1` rows.

        Phase 14: without a contract the tenant mechanism is unknown, so no business read
        runs at all. `mechanism=column`: the reserved `:tenant` parameter is always the
        given tenant and a query that does not use it is refused.
        `mechanism=rls_session_setting`: the confirmed setting is set for the transaction."""
        isolation = self._contract.tenant_isolation if self._contract is not None else None
        if isolation is None:
            raise ErpSafetyViolationError("No verified tenant isolation: business reads refused.")
        if not tenant_id:
            raise ErpSafetyViolationError("A business read needs the principal's tenant.")
        bound = dict(params or {})
        setting: str | None = None
        if isolation.mechanism == "column":
            if ":tenant" not in query.sql:
                raise ErpSafetyViolationError(f"{query.name}: no tenant filter (:tenant).")
            bound["tenant"] = tenant_id  # never a caller-supplied value
        else:
            setting = isolation.setting
        await self.verify()
        return await self._execute(
            query, bound, max_rows=max_rows or self._max_rows,
            tenant_setting=(setting, tenant_id) if setting else None,
        )  # fmt: skip

    async def _execute(
        self,
        query: ErpQuery,
        params: Mapping[str, Any],
        *,
        max_rows: int,
        tenant_setting: tuple[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(query, ErpQuery):
            raise ErpSafetyViolationError("Only ErpQuery constants may run against the ERP.")
        check_read_only_sql(query.sql)  # again: a frozen dataclass can still be forged
        try:
            async with self._db.unit_of_work(read_only=True) as uow:
                if tenant_setting is not None:
                    # The one statement outside the guard: the setting name comes from the
                    # validated contract (identifier pattern), the value is bound, and
                    # `is_local=true` limits it to this read-only transaction.
                    await uow.session.execute(
                        text(_TENANT_BINDING_SQL),
                        {"name": tenant_setting[0], "value": tenant_setting[1]},
                    )
                result = await uow.session.execute(text(query.sql), dict(params))
                rows = result.mappings().fetchmany(max_rows + 1)
                return [dict(row) for row in rows]
        except DatabaseTimeoutError as exc:
            raise ErpTimeoutError(f"{query.name}: {exc.detail}") from exc
        except (DatabaseReadOnlyError, DatabasePermissionError) as exc:
            logger.error(
                "erp.write_or_permission_refused",
                extra={"fields": {"query": query.name, "code": exc.code}},
            )
            raise ErpSafetyViolationError(f"{query.name}: {exc.code}") from exc
        except DatabaseError as exc:
            raise ErpUnavailableError(f"{query.name}: {exc.code}") from exc

    async def dispose(self) -> None:
        await self._db.dispose()


# --- adapter ---------------------------------------------------------------------------


_TENANT_BINDING_SQL = "SELECT set_config(:name, :value, true)"

# Which capability's verified mapping a port method needs. Name/ref resolution serves the
# capabilities that resolve that entity.
METHOD_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "find_customers": ("customer_outstanding", "customer_open_invoices"),
    "get_customer": ("customer_outstanding", "customer_open_invoices"),
    "customer_outstanding": ("customer_outstanding",),
    "open_invoices": ("customer_open_invoices",),
    "daily_sales": ("daily_sales",),
    "sales_by_day": ("daily_sales",),
    "find_products": ("product_stock", "low_stock_products"),
    "get_product": ("product_stock", "low_stock_products"),
    "find_warehouses": ("warehouse_stock",),
    "get_warehouse": ("warehouse_stock",),
    "product_stock": ("product_stock",),
    "warehouse_stock": ("warehouse_stock",),
    "low_stock": ("low_stock_products",),
    "customer_count": ("customer_count",),
    "customers_billed": ("customer_count",),
}


class CapabilityReader(Protocol):
    """Reviewed read code for one capability: implements the port methods it serves with
    `ErpQuery` constants and `ReadOnlyErpDatabase.fetch(..., tenant_id=ctx.tenant_id)`,
    returning only the platform record fields (data minimization)."""

    capability: str


class PostgresErpAdapter:
    """The live adapter. A capability is answered only by a reader registered for it,
    which needs a VERIFIED contract and a VERIFIED mapping. No reader exists yet (Q1), so
    every capability is UNAVAILABLE / RULE_NOT_VERIFIED and nothing is sent to the ERP."""

    name = "postgres"
    test_data = False
    # No ERP rule is confirmed without a VERIFIED contract (Q1): no derived figure is
    # computed. An instance with a VERIFIED contract lists the rules the owner confirmed.
    verified_rules: frozenset[str] = frozenset()

    def __init__(
        self,
        database: ReadOnlyErpDatabase | Mapping[str, ReadOnlyErpDatabase],
        contract: ErpContract | None = None,
    ) -> None:
        # One database, or (multi-database contract) one per contract database key.
        self._databases: dict[str | None, ReadOnlyErpDatabase] = (
            {key: db for key, db in database.items()}
            if isinstance(database, Mapping)
            else {None: database}
        )
        self._contract = contract
        self._readers: dict[str, CapabilityReader] = {}
        if contract is not None:
            self.verified_rules = contract.verified_business_rules()

    def gate_state(self) -> ErpGateState:
        """Phase 15: where this adapter stands, from memory only (never connects)."""
        contract = self._contract
        if contract is None or not any(
            contract.capability_verified(name) for name in contract.capabilities
        ):
            return "unverified"  # nothing can be answered: every capability RULE_NOT_VERIFIED
        if any(db.refused for db in self._databases.values()):
            return "refused"
        return "verified" if all(db.verified for db in self._databases.values()) else "pending"

    @property
    def databases(self) -> Mapping[str | None, ReadOnlyErpDatabase]:
        """The read-only connections, by contract database key (readers use them)."""
        return self._databases

    async def aclose(self) -> None:
        """Phase 15: close the read-only pools (process shutdown). Reconnect lazily."""
        for db in self._databases.values():
            await db.dispose()

    def register_reader(self, reader: CapabilityReader) -> None:
        """Activate one capability. Refused unless its mapping is VERIFIED (Phase 14)."""
        if self._contract is None or not self._contract.capability_verified(reader.capability):
            raise ConfigurationError(
                f"{reader.capability}: no VERIFIED ERP contract mapping; reader refused."
            )
        self._readers[reader.capability] = reader

    def _reader(self, method: str) -> Any:
        for capability in METHOD_CAPABILITIES[method]:
            reader = self._readers.get(capability)
            if reader is not None and hasattr(reader, method):
                return getattr(reader, method)
        raise self._unmapped(method)

    @staticmethod
    def _unmapped(capability: str) -> ErpRuleNotVerifiedError:
        return ErpRuleNotVerifiedError(
            f"{capability}: no verified ERP schema mapping (PROJECT_STATUS Q1)"
        )

    async def find_customers(
        self, ctx: BusinessContext, name: str, limit: int
    ) -> Sequence[CustomerRecord]:
        return await self._reader("find_customers")(ctx, name, limit)  # type: ignore[no-any-return]

    async def get_customer(self, ctx: BusinessContext, ref: str) -> CustomerRecord | None:
        return await self._reader("get_customer")(ctx, ref)  # type: ignore[no-any-return]

    async def customer_outstanding(
        self, ctx: BusinessContext, customer_ref: str
    ) -> OutstandingRecord | None:
        return await self._reader("customer_outstanding")(ctx, customer_ref)  # type: ignore[no-any-return]

    async def open_invoices(
        self,
        ctx: BusinessContext,
        customer_ref: str | None,
        limit: int,
        invoice_number: str | None = None,
    ) -> Sequence[InvoiceRecord]:
        return await self._reader("open_invoices")(ctx, customer_ref, limit, invoice_number)  # type: ignore[no-any-return]

    async def daily_sales(self, ctx: BusinessContext, business_date: date) -> SalesRecord | None:
        return await self._reader("daily_sales")(ctx, business_date)  # type: ignore[no-any-return]

    async def sales_by_day(
        self, ctx: BusinessContext, start: date, end: date, limit: int
    ) -> Sequence[SalesRecord]:
        return await self._reader("sales_by_day")(ctx, start, end, limit)  # type: ignore[no-any-return]

    async def find_products(
        self, ctx: BusinessContext, name: str, limit: int
    ) -> Sequence[ProductRecord]:
        return await self._reader("find_products")(ctx, name, limit)  # type: ignore[no-any-return]

    async def get_product(self, ctx: BusinessContext, ref: str) -> ProductRecord | None:
        return await self._reader("get_product")(ctx, ref)  # type: ignore[no-any-return]

    async def find_warehouses(
        self, ctx: BusinessContext, name: str, limit: int
    ) -> Sequence[WarehouseRecord]:
        return await self._reader("find_warehouses")(ctx, name, limit)  # type: ignore[no-any-return]

    async def get_warehouse(self, ctx: BusinessContext, ref: str) -> WarehouseRecord | None:
        return await self._reader("get_warehouse")(ctx, ref)  # type: ignore[no-any-return]

    async def product_stock(
        self, ctx: BusinessContext, product_ref: str, limit: int
    ) -> Sequence[StockRecord]:
        return await self._reader("product_stock")(ctx, product_ref, limit)  # type: ignore[no-any-return]

    async def warehouse_stock(
        self, ctx: BusinessContext, warehouse_ref: str, limit: int
    ) -> Sequence[StockRecord]:
        return await self._reader("warehouse_stock")(ctx, warehouse_ref, limit)  # type: ignore[no-any-return]

    async def low_stock(
        self, ctx: BusinessContext, limit: int, product_ref: str | None = None
    ) -> Sequence[LowStockRecord]:
        return await self._reader("low_stock")(ctx, limit, product_ref)  # type: ignore[no-any-return]

    async def customer_count(self, ctx: BusinessContext) -> CustomerCountRecord | None:
        return await self._reader("customer_count")(ctx)  # type: ignore[no-any-return]

    async def customers_billed(
        self, ctx: BusinessContext, start: date, end: date
    ) -> CustomersBilledRecord | None:
        return await self._reader("customers_billed")(ctx, start, end)  # type: ignore[no-any-return]
