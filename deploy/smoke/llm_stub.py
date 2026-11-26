"""Messages-API stand-in for the DEPLOYMENT SMOKE TEST ONLY (Phase 15). Standard library only.

The API reaches it through `LLM_ANTHROPIC_BASE_URL`, so the real Anthropic SDK, the
`LLMProvider` abstraction, LangGraph, the Supervisor, the business domain (ERP boundary),
checkpoints and audit all run unchanged; only the model is replaced by fixed outputs:

* planner (structured output) calls: a business classification when the user message
  mentions "outstanding" (customer outstanding of the TEST customer "Rahul Gupta"),
  otherwise a greeting with no tool;
* writer (text) calls: a fixed reply for each of those two cases.

A user message containing ``SLOW-<seconds>`` makes the planner call take that long (at
most 120 s): the smoke test uses it to keep a turn in flight while the API shuts down.

    python llm_stub.py          # STUB_PORT (8090)

Never point a production or staging deployment at this. Like deploy/loadtest/llm_stub.py,
it exists only so a local stack can run without a credential.
"""

from __future__ import annotations

import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PORT = int(os.environ.get("STUB_PORT", "8090"))
_SLOW = re.compile(r"SLOW-(\d{1,3})")

GREETING = {
    "intent": "greeting",
    "domain": "general",
    "summary": "The user greets the assistant.",
    "needs_clarification": False,
    "clarification_question": None,
    "tool_calls": [],
}
BUSINESS = {
    "intent": "customer_outstanding",
    "domain": "business",
    "summary": "The user asks for a customer's outstanding balance.",
    "needs_clarification": False,
    "clarification_question": None,
    "tool_calls": [
        {
            "tool_name": "business.customer_outstanding",
            "arguments_json": json.dumps({"customer_name": "Rahul Gupta"}),
        }
    ],
}
GREETING_REPLY = "Hello! (smoke-test stub answer)"
BUSINESS_REPLY = "Rahul Gupta ka outstanding 45,000.00 INR hai (TEST data)."


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return ""


def _user_text(body: dict[str, Any]) -> str:
    """The last user message: the planner sees the question there; the writer sees the
    question plus the tool results."""
    for message in reversed(body.get("messages") or []):
        if isinstance(message, dict) and message.get("role") == "user":
            return _text_of(message.get("content"))
    return ""


def message(text: str) -> dict[str, Any]:
    return {
        "id": "msg_smoke_stub",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 50, "output_tokens": 20},
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        pass

    def do_POST(self) -> None:  # http.server API
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        structured = "output_format" in body or "format" in (body.get("output_config") or {})
        user = _user_text(body)
        business = "outstanding" in user.lower()
        if structured:
            slow = _SLOW.search(user)
            if slow:
                time.sleep(min(int(slow.group(1)), 120))
            text = json.dumps(BUSINESS if business else GREETING)
        else:
            text = BUSINESS_REPLY if business else GREETING_REPLY
        raw = json.dumps(message(text)).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("request-id", "req_smoke_stub")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print(f"smoke llm stub on :{PORT}", flush=True)
    server.serve_forever()
