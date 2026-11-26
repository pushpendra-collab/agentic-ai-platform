"""HTTP API (Phase 6): a thin boundary around the existing agent and services.

    python -m agentic_platform.api            # serve (API_HOST / API_PORT)
    python -m agentic_platform.api --openapi  # print the OpenAPI schema
"""

from agentic_platform.api.app import API_VERSION, create_app, runtime_of

__all__ = ["API_VERSION", "create_app", "runtime_of"]
