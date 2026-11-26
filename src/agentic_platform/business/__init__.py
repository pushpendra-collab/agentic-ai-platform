"""Business integration layer (Phase 10, ADR-059): read-only, typed, schema-safe.

    LangGraph → business tool → BusinessReadService → ErpReadPort → adapter → ERP

See docs/BUSINESS_INTEGRATION.md. No ERP table, column or business rule is assumed here;
a live adapter maps the confirmed ERP schema onto these contracts (Q1).
"""
