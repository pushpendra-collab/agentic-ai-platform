"""Behaviour every memory storage adapter must support, exercised through the
memory types. Run against InMemoryMemoryStore (unit tests) and
PostgresMemoryStore (integration tests, as the RLS-restricted app role), so a
store can be swapped without changing memory behaviour."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest

from agentic_platform.cache import InMemoryBackend, TemporaryStateStore
from agentic_platform.core.config import AppSettings, MemorySettings
from agentic_platform.core.errors import MemoryPolicyError
from agentic_platform.memory import Episode, MemoryKind, MemoryManager, MemoryQuery, MemoryScope
from agentic_platform.memory.embeddings import HashingEmbedder
from agentic_platform.prompts import PromptRegistry

ContractCheck = Callable[[MemoryManager], Awaitable[None]]


def make_manager(store: Any, **memory: Any) -> MemoryManager:
    settings = AppSettings(memory=MemorySettings(**memory))
    return MemoryManager(
        store=store,
        vectors=store,
        embedder=HashingEmbedder(settings.memory.embedding_dimensions),
        working_store=TemporaryStateStore(InMemoryBackend(), default_ttl_seconds=60),
        prompts=PromptRegistry(),
        settings=settings,
    )


def tenant() -> str:
    """Unique per check, so checks never see each other's rows in a shared database."""
    return f"t-{uuid.uuid4().hex[:12]}"


async def check_long_term_upsert_get_forget(m: MemoryManager) -> None:
    alice = MemoryScope(tenant(), "alice")
    first = await m.long_term.remember(alice, "prefs", "language", "Prefers replies in Hindi.")
    second = await m.long_term.remember(alice, "prefs", "language", "Prefers replies in Tamil.")
    assert second.id == first.id  # same (namespace, key): updated in place
    got = await m.long_term.get(alice, "prefs", "language")
    assert got is not None and got.content == "Prefers replies in Tamil."
    assert got.user_id == "alice" and got.kind is MemoryKind.LONG_TERM
    assert got.embedding_model == "hashing-v1-256"
    assert await m.long_term.forget(alice, "prefs", "language") is True
    assert await m.long_term.get(alice, "prefs", "language") is None
    assert await m.long_term.forget(alice, "prefs", "language") is False


async def check_user_and_tenant_isolation(m: MemoryManager) -> None:
    t = tenant()
    alice, bob = MemoryScope(t, "alice"), MemoryScope(t, "bob")
    other_tenant = MemoryScope(tenant(), "alice")  # same user id, different company

    await m.long_term.remember(alice, "prefs", "tone", "Alice likes formal replies.")
    await m.long_term.remember(
        alice, "company", "fiscal_year", "Fiscal year starts in April.", shared=True
    )

    assert await m.long_term.get(bob, "prefs", "tone") is None  # private to alice
    bob_facts = {r.content for r in await m.long_term.list_facts(bob)}
    assert bob_facts == {"Fiscal year starts in April."}  # shared within the tenant
    assert await m.long_term.list_facts(other_tenant) == []  # nothing crosses tenants
    hits = await m.long_term.search(other_tenant, "formal replies fiscal year", k=10)
    assert hits == []


async def check_long_term_search_ranks_relevant_first(m: MemoryManager) -> None:
    s = MemoryScope(tenant(), "u1")
    await m.long_term.remember(s, "prefs", "language", "User prefers replies in Hindi language.")
    await m.long_term.remember(s, "prefs", "reports", "Weekly sales reports are due on Friday.")
    hits = await m.long_term.search(s, "which language should replies use", k=2)
    assert hits[0].record.key == "language"
    assert hits[0].score > hits[1].score


async def check_ttl_expiry_and_purge(m: MemoryManager) -> None:
    s = MemoryScope(tenant(), "u1")
    await m.long_term.remember(s, "tmp", "k", "short lived fact", ttl=timedelta(seconds=1))
    assert await m.long_term.get(s, "tmp", "k") is not None
    await asyncio.sleep(1.2)
    assert await m.long_term.get(s, "tmp", "k") is None  # expired rows are invisible
    assert await m.purge_expired() >= 1  # and the retention job deletes them


async def check_semantic_ingest_search_replace_delete(m: MemoryManager) -> None:
    t = tenant()
    alice, bob = MemoryScope(t, "alice"), MemoryScope(t, "bob")
    handbook = (
        "Leave policy. Employees get 18 days of paid leave per year.\n\n"
        "Travel policy. Economy class is used for flights under six hours.\n\n"
        + "Filler paragraph about office plants and coffee. "
        * 40
    )
    chunks = await m.semantic.ingest(alice, source_ref="handbook", text=handbook, title="HR")
    assert len(chunks) >= 2 and all(c.user_id is None for c in chunks)  # tenant-shared
    assert {c.metadata["chunk_count"] for c in chunks} == {len(chunks)}

    hits = await m.semantic.search(bob, "how many days of paid leave", k=3)
    assert hits and "18 days" in hits[0].record.content
    assert hits[0].record.source_ref == "handbook"

    replaced = await m.semantic.ingest(alice, source_ref="handbook", text="Leave is 20 days.")
    assert len(replaced) == 1
    assert [c.content for c in await m.semantic.chunks(bob, "handbook")] == ["Leave is 20 days."]

    assert await m.semantic.delete_source(alice, "handbook") == 1
    assert await m.semantic.search(bob, "paid leave", k=3) == []


async def check_semantic_private_documents(m: MemoryManager) -> None:
    t = tenant()
    alice, bob = MemoryScope(t, "alice"), MemoryScope(t, "bob")
    await m.semantic.ingest(alice, source_ref="notes", text="Alice private note.", shared=False)
    assert await m.semantic.search(bob, "Alice private note", k=5) == []
    assert len(await m.semantic.search(alice, "Alice private note", k=5)) == 1


async def check_semantic_refuses_business_records(m: MemoryManager) -> None:
    s = MemoryScope(tenant(), "u1")
    with pytest.raises(MemoryPolicyError):
        await m.semantic.ingest(s, source_ref="inv-1", text="Invoice 1, 500", source_type="invoice")


async def check_episodic_record_recall_forget(m: MemoryManager) -> None:
    t = tenant()
    alice, bob = MemoryScope(t, "alice"), MemoryScope(t, "bob")
    for i, (summary, thread) in enumerate(
        [("User asked for the current time in Kolkata", "th-1"), ("User said thanks", "th-2")]
    ):
        await m.episodic.record(
            alice,
            Episode(
                thread_id=thread,
                request_id=f"r{i}",
                intent="x",
                outcome="completed",
                summary=summary,
                tools=["system.current_time"] if i == 0 else [],
            ),
        )
    hits = await m.episodic.recall(alice, "what time is it in Kolkata", k=1)
    assert hits[0].record.thread_id == "th-1"
    assert hits[0].record.metadata["tools"] == ["system.current_time"]
    assert hits[0].record.expires_at is not None  # episodes always expire
    assert await m.episodic.recall(bob, "time in Kolkata", k=5) == []  # personal
    assert [r.thread_id for r in await m.episodic.recent(alice, limit=5)] == ["th-2", "th-1"]
    assert await m.episodic.forget(alice, thread_id="th-1") == 1
    assert [r.thread_id for r in await m.episodic.recent(alice)] == ["th-2"]


async def check_procedural_versions(m: MemoryManager) -> None:
    t = tenant()
    s = MemoryScope(t, "admin")
    builtin = await m.procedural.get(s, "generate_response.system")
    assert builtin is not None and builtin.source == "builtin"

    v1 = await m.procedural.define(s, "greeting", "Greet formally.")
    v2 = await m.procedural.define(s, "greeting", "Greet warmly.")
    assert (v1.version, v2.version) == (1, 2)
    latest = await m.procedural.get(MemoryScope(t, "someone-else"), "greeting")
    assert latest is not None and (latest.version, latest.content) == (2, "Greet warmly.")
    first = await m.procedural.get(s, "greeting", version=1)
    assert first is not None and first.content == "Greet formally."
    assert [r.version for r in await m.procedural.versions(s, "greeting")] == [1, 2]
    assert await m.procedural.get(MemoryScope(tenant(), "x"), "greeting") is None
    assert await m.procedural.delete_version(s, "greeting", 2) is True
    after = await m.procedural.get(s, "greeting")
    assert after is not None and after.version == 1


async def check_conversation_summary(m: MemoryManager) -> None:
    s = MemoryScope(tenant(), "u1")
    assert await m.conversation.summary(s, "th-9") is None
    first = await m.conversation.save_summary(s, "th-9", "Discussed leave policy.")
    second = await m.conversation.save_summary(s, "th-9", "Discussed leave and travel.")
    assert first.id == second.id
    got = await m.conversation.summary(s, "th-9")
    assert got is not None and got.content == "Discussed leave and travel."
    assert await m.conversation.summary(MemoryScope(tenant(), "u1"), "th-9") is None


async def check_update_and_forget_user(m: MemoryManager) -> None:
    t = tenant()
    alice, bob = MemoryScope(t, "alice"), MemoryScope(t, "bob")
    fact = await m.long_term.remember(alice, "prefs", "x", "old text")
    store = m._store
    assert await store.update(bob, fact.id, {"content": "hijack"}) is None  # not bob's
    updated = await store.update(alice, fact.id, {"content": "new text", "metadata": {"a": 1}})
    assert updated is not None and updated.content == "new text" and updated.metadata == {"a": 1}
    with pytest.raises(MemoryPolicyError):
        await store.update(alice, fact.id, {"tenant_id": "other"})

    await m.long_term.remember(alice, "company", "shared", "Shared fact.", shared=True)
    await m.episodic.record(
        alice, Episode(thread_id="t", request_id="r", outcome="completed", summary="hi")
    )
    assert await m.forget_user(alice) == 2  # her fact and her episode
    remaining = await store.find(bob, MemoryQuery(kinds=tuple(MemoryKind), include_shared=True))
    assert [r.content for r in remaining] == ["Shared fact."]  # tenant memory kept


CONTRACT: list[ContractCheck] = [
    check_long_term_upsert_get_forget,
    check_user_and_tenant_isolation,
    check_long_term_search_ranks_relevant_first,
    check_ttl_expiry_and_purge,
    check_semantic_ingest_search_replace_delete,
    check_semantic_private_documents,
    check_semantic_refuses_business_records,
    check_episodic_record_recall_forget,
    check_procedural_versions,
    check_conversation_summary,
    check_update_and_forget_user,
]
