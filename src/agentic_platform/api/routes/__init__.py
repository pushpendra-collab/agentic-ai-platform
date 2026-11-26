"""Versioned API routes (`/api/v1`) and unversioned probes (`/health`, `/ready`)."""

from fastapi import APIRouter

from agentic_platform.api.routes import agent, approvals, conversations, memory, system

API_PREFIX = "/api/v1"

api_v1 = APIRouter(prefix=API_PREFIX)
api_v1.include_router(system.identity_router)
api_v1.include_router(agent.router)
api_v1.include_router(conversations.router)
api_v1.include_router(approvals.router)
api_v1.include_router(memory.router)
api_v1.include_router(system.audit_router)

probes = system.probe_router

__all__ = ["API_PREFIX", "api_v1", "probes"]
