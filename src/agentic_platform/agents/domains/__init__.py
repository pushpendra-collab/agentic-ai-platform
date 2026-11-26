"""Domain handlers: the extension point for future domain agents.

`process_request` dispatches to the handler registered for the domain the
request was routed to. Phase 1 registers only `general`. A future Purchase,
Sales, Inventory, Accounting or GST agent is added by implementing
`DomainHandler` (typically wrapping a compiled LangGraph subgraph) and
registering it; the top-level graph does not change.
"""

from agentic_platform.agents.domains.base import DomainHandler, DomainRegistry, DomainRequest
from agentic_platform.agents.domains.general import GeneralDomainHandler

__all__ = ["DomainHandler", "DomainRegistry", "DomainRequest", "GeneralDomainHandler"]
