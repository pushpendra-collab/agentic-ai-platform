"""Messages-API stand-in for LOAD TESTS ONLY (Phase 8). Standard library only.

The API talks to it through `LLM_ANTHROPIC_BASE_URL`, so the real Anthropic SDK, the
`LLMProvider` abstraction, LangGraph, memory, checkpoints and audit all run unchanged;
only the model is replaced by a fixed answer after a fixed delay. Results therefore
measure the PLATFORM, not Claude: real model latency (seconds) dominates real turns.

    python llm_stub.py          # STUB_PORT (8090), STUB_LATENCY_MS (default 500)

Never point a production or staging deployment at this.
"""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

LATENCY = int(os.environ.get("STUB_LATENCY_MS", "500")) / 1000
PORT = int(os.environ.get("STUB_PORT", "8090"))

UNDERSTANDING = {
    "intent": "greeting",
    "domain": "general",
    "summary": "The user greets the assistant.",
    "needs_clarification": False,
    "clarification_question": None,
    "tool_calls": [],
}


def message(text: str) -> dict[str, Any]:
    return {
        "id": "msg_loadtest_stub",
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
        time.sleep(LATENCY)
        text = json.dumps(UNDERSTANDING) if structured else "Hello! (load-test stub answer)"
        raw = json.dumps(message(text)).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("request-id", "req_loadtest_stub")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print(f"llm stub on :{PORT}, latency {LATENCY}s", flush=True)
    server.serve_forever()
