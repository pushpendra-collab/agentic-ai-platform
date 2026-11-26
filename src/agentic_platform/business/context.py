"""Trusted execution context of a business read (Phase 10, ADR-059).

`BusinessContext` is built by the service from the authenticated `Principal` only. Tool
arguments, the conversation and LLM output can never set or widen the tenant, the user,
the warehouse scope or the permissions (tool inputs reject unknown fields; none of them
has such a field).

Warehouse scope: the ERP's warehouse-assignment mechanism is not known (Q1). The
platform therefore decides scope from its own permission model and fails closed:

* `business.warehouse.all` → every warehouse of the tenant (`ALL`);
* otherwise → no warehouse (`NONE`), unless a `WarehouseScopeResolver` supplied by a
  later phase maps the principal to a listed set (`LISTED`).

How a platform tenant maps to the ERP's tenant (column, RLS setting, schema or database)
is also part of the missing ERP contract; adapters receive the platform tenant id and
own that mapping.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from agentic_platform.business.capabilities import BusinessPermission
from agentic_platform.security.identity import Principal


class ScopeKind(StrEnum):
    ALL = "all"
    LISTED = "listed"
    NONE = "none"


class WarehouseScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ScopeKind
    warehouse_refs: frozenset[str] = frozenset()

    def allows(self, warehouse_ref: str) -> bool:
        if self.kind is ScopeKind.ALL:
            return True
        return self.kind is ScopeKind.LISTED and warehouse_ref in self.warehouse_refs

    @property
    def empty(self) -> bool:
        return self.kind is ScopeKind.NONE or (
            self.kind is ScopeKind.LISTED and not self.warehouse_refs
        )

    @property
    def limited(self) -> bool:
        return self.kind is not ScopeKind.ALL


ALL_WAREHOUSES = WarehouseScope(kind=ScopeKind.ALL)
NO_WAREHOUSES = WarehouseScope(kind=ScopeKind.NONE)


class WarehouseScopeResolver(Protocol):
    def resolve(self, principal: Principal) -> WarehouseScope: ...


class PermissionScopeResolver:
    """Default: all warehouses with `business.warehouse.all`, otherwise none."""

    def resolve(self, principal: Principal) -> WarehouseScope:
        if principal.has(BusinessPermission.WAREHOUSE_ALL):
            return ALL_WAREHOUSES
        return NO_WAREHOUSES


class BusinessContext(BaseModel):
    """Who a read runs for. Created by `BusinessReadService` from the principal only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    user_id: str
    warehouse_scope: WarehouseScope

    @classmethod
    def for_principal(
        cls, principal: Principal, resolver: WarehouseScopeResolver
    ) -> BusinessContext:
        return cls(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            warehouse_scope=resolver.resolve(principal),
        )
