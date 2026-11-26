"""Build the configured ERP read port (Phase 10, ADR-059; multi-database ERP 2026-10-30)."""

from __future__ import annotations

from agentic_platform.business.adapters.invenlogic import register_invenlogic_readers
from agentic_platform.business.adapters.postgres import PostgresErpAdapter, ReadOnlyErpDatabase
from agentic_platform.business.erp_contract import ContractError, ErpContract, load_contract
from agentic_platform.business.ports import ErpReadPort
from agentic_platform.core.config import AppSettings
from agentic_platform.core.errors import ConfigurationError
from agentic_platform.core.logging import get_logger
from agentic_platform.services.clock import ClockService

logger = get_logger(__name__)


def build_erp_port(settings: AppSettings, clock: ClockService) -> ErpReadPort | None:
    """None for `ERP_ADAPTER=none` (the business layer is then not registered).

    `clock` is kept for the call sites; the live adapter reads dates from the ERP."""
    del clock
    erp = settings.erp
    if erp.adapter == "none":
        return None
    agent_urls = [
        secret.get_secret_value()
        for secret in (settings.database.url, settings.database.migration_url)
        if secret is not None
    ]
    # Phase 14 (ADR-063): a configured contract must load and validate, or startup fails.
    # Without one the adapter has no tenant mechanism and runs no business read at all.
    contract: ErpContract | None = None
    if erp.contract_path is not None:
        try:
            contract = load_contract(erp.contract_path)
        except ContractError as exc:
            raise ConfigurationError(str(exc)) from None
    urls = erp.database_url_map()
    if contract is not None and contract.databases:
        # A multi-database ERP: one read-only connection (and gate) per contract database.
        if set(urls) != set(contract.databases):
            raise ConfigurationError(
                "ERP_DATABASE_URLS must name exactly the contract's databases: "
                + ", ".join(sorted(contract.databases))
            )
        databases = {
            key: ReadOnlyErpDatabase(
                erp,
                agent_database_urls=agent_urls,
                contract=contract,
                database_key=key,
                url=urls[key],
            )
            for key in sorted(contract.databases)
        }
        adapter = PostgresErpAdapter(databases, contract)
    else:
        if urls:
            raise ConfigurationError(
                "ERP_DATABASE_URLS needs a contract with `databases`; use ERP_DATABASE_URL."
            )
        adapter = PostgresErpAdapter(
            ReadOnlyErpDatabase(erp, agent_database_urls=agent_urls, contract=contract), contract
        )
    if contract is not None:
        active = register_invenlogic_readers(adapter, contract)
        logger.info(
            "erp.readers.registered",
            extra={"fields": {"reader_set": contract.reader_set, "capabilities": active}},
        )
    return adapter
