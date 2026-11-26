"""Graph nodes. Each module exposes a `make_<node>(deps)` factory returning the node."""

from agentic_platform.agents.nodes.approval_gate import make_approval_gate
from agentic_platform.agents.nodes.finalize_turn import make_finalize_turn
from agentic_platform.agents.nodes.generate_response import make_generate_response
from agentic_platform.agents.nodes.handle_error import make_handle_error
from agentic_platform.agents.nodes.process_request import make_process_request
from agentic_platform.agents.nodes.supervise import make_supervise
from agentic_platform.agents.nodes.understand_request import make_understand_request
from agentic_platform.agents.nodes.verify_business_response import (
    make_verify_business_response,
)

UNDERSTAND_REQUEST = "understand_request"
SUPERVISE = "supervise"
PROCESS_REQUEST = "process_request"
GENERATE_RESPONSE = "generate_response"
HANDLE_ERROR = "handle_error"
APPROVAL_GATE = "approval_gate"
VERIFY_BUSINESS_RESPONSE = "verify_business_response"
FINALIZE_TURN = "finalize_turn"

__all__ = [
    "APPROVAL_GATE",
    "FINALIZE_TURN",
    "GENERATE_RESPONSE",
    "HANDLE_ERROR",
    "PROCESS_REQUEST",
    "SUPERVISE",
    "UNDERSTAND_REQUEST",
    "VERIFY_BUSINESS_RESPONSE",
    "make_approval_gate",
    "make_finalize_turn",
    "make_generate_response",
    "make_handle_error",
    "make_process_request",
    "make_supervise",
    "make_understand_request",
    "make_verify_business_response",
]
