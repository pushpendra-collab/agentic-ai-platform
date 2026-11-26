"""Row-level security session settings (ADR-034, ADR-041).

Tenant-scoped tables have RLS policies that compare each row's tenant with
`current_setting('app.tenant_id')`. Repositories call `bind_tenant()` at the
start of every transaction, in addition to filtering on `tenant_id` explicitly,
so a missing filter (an application bug) still cannot cross tenants.

`set_config(..., is_local => true)` lasts until the end of the transaction, so
a pooled connection never carries one tenant's binding into another request.

`enable_maintenance()` is for cross-tenant maintenance jobs (expiry, retention).
`memory_item` keeps its Phase 4 flag (`app.memory_maintenance`); Phase 5 tables
use `app.maintenance`.
"""

from __future__ import annotations

from sqlalchemy import Text, bindparam, func, select
from sqlalchemy.ext.asyncio import AsyncSession

TENANT_SETTING = "app.tenant_id"
MAINTENANCE_SETTING = "app.maintenance"


async def bind_tenant(session: AsyncSession, tenant_id: str) -> None:
    await session.execute(
        select(func.set_config(TENANT_SETTING, bindparam("tenant", tenant_id, Text), True))
    )


async def enable_maintenance(session: AsyncSession, setting: str = MAINTENANCE_SETTING) -> None:
    await session.execute(select(func.set_config(setting, "on", True)))
