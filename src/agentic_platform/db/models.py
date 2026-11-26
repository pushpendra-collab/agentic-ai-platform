"""Import every ORM model module here so `Base.metadata` is complete for Alembic.

Phases 3-5 add platform tables only (checkpoints, audit log, idempotency,
agent memory, conversation ownership, approval requests). There are still no
business tables. When a domain adds models, import its module below, for example:

    from agentic_platform.domains.purchase import models as _purchase_models  # noqa: F401
"""

from agentic_platform.db import approvals as _approvals  # noqa: F401
from agentic_platform.db import audit as _audit  # noqa: F401
from agentic_platform.db import checkpoints as _checkpoints  # noqa: F401
from agentic_platform.db import conversations as _conversations  # noqa: F401
from agentic_platform.db import idempotency as _idempotency  # noqa: F401
from agentic_platform.db import memory as _memory  # noqa: F401
from agentic_platform.db.base import Base

target_metadata = Base.metadata

PLATFORM_TABLES = frozenset(
    {
        "agent_checkpoints",
        "agent_checkpoint_blobs",
        "agent_checkpoint_writes",
        "approval_request",
        "audit_log",
        "conversation_thread",
        "idempotency_record",
        "memory_item",
    }
)
