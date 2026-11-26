"""Agentic AI platform foundation.

Layering (top to bottom; each layer only calls the one below it):

    agents (entry point)  ->  graph  ->  nodes  ->  tools  ->  services

The LLM never touches services or data directly; it can only act through
registered tools (see ARCHITECTURE.md, ADR-004 and ADR-005).
"""

__version__ = "0.1.0"
