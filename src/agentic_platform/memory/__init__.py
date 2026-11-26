"""Agent memory (ADR-033 to ADR-036).

    Agent → MemoryManager → memory types → storage adapters (via ports.py)

| Memory type        | Module            | Storage                                              |
|--------------------|-------------------|------------------------------------------------------|
| Working/short-term | working.py        | Redis (TemporaryStateStore) + graph `messages` window |
| Conversation       | conversation.py   | LangGraph checkpointer + PostgreSQL summary record   |
| Long-term          | long_term.py      | PostgreSQL (+ pgvector for recall)                   |
| Semantic           | semantic.py       | PostgreSQL + pgvector                                |
| Episodic           | episodic.py       | PostgreSQL + pgvector, expiring                      |
| Procedural         | procedural.py     | Prompt files (built-in) + PostgreSQL versions        |
| Checkpoint state   | checkpoints.py    | LangGraph checkpointer (PostgreSQL)                  |

Every operation is scoped by `MemoryScope(tenant_id, user_id)`.
"""

from agentic_platform.memory.manager import MemoryManager, build_memory_manager
from agentic_platform.memory.models import (
    Episode,
    MemoryContext,
    MemoryKind,
    MemoryQuery,
    MemoryRecord,
    MemoryScope,
    NewMemory,
    ScoredMemory,
    VectorQuery,
)

__all__ = [
    "Episode",
    "MemoryContext",
    "MemoryKind",
    "MemoryManager",
    "MemoryQuery",
    "MemoryRecord",
    "MemoryScope",
    "NewMemory",
    "ScoredMemory",
    "VectorQuery",
    "build_memory_manager",
]
