"""Memory system tests without external servers: in-memory store + hashing embedder
+ in-memory working store."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentic_platform.agents import Agent
from agentic_platform.agents.dependencies import build_dependencies
from agentic_platform.cache import InMemoryBackend, TemporaryStateStore
from agentic_platform.core.config import AppSettings, MemorySettings
from agentic_platform.core.errors import (
    ConfigurationError,
    EmbeddingError,
    EmbeddingUnavailableError,
    InvalidMemoryScopeError,
)
from agentic_platform.memory import MemoryContext, MemoryManager, MemoryScope
from agentic_platform.memory.adapters.in_memory import InMemoryMemoryStore, cosine
from agentic_platform.memory.chunking import chunk_text
from agentic_platform.memory.embeddings import HashingEmbedder, VoyageEmbedder, create_embedder
from agentic_platform.memory.working import WorkingMemory
from agentic_platform.services.clock import ClockService
from tests.conftest import LOCAL_TENANT, principal, understanding
from tests.fakes import ScriptedLLM
from tests.memory_contract import CONTRACT, ContractCheck, make_manager

# --- the storage contract, on the in-memory adapter ----------------------------------------


@pytest.mark.parametrize("check", CONTRACT, ids=lambda c: c.__name__)
def test_in_memory_store_contract(check: ContractCheck) -> None:
    asyncio.run(check(make_manager(InMemoryMemoryStore())))


# --- scope --------------------------------------------------------------------------------


def test_scope_validation_and_user_rules() -> None:
    scope = MemoryScope("acme", "asha")
    assert scope.user_key == "asha"
    assert scope.readable_user_keys(include_shared=True) == ("", "asha")
    assert MemoryScope("acme").readable_user_keys(include_shared=False) == ("",)
    for bad in [("", None), ("has space", None), ("acme", "x/y"), ("a" * 129, None)]:
        with pytest.raises(InvalidMemoryScopeError):
            MemoryScope(*bad)


# --- embeddings ---------------------------------------------------------------------------


def test_hashing_embedder_is_deterministic_normalised_and_lexical() -> None:
    async def body() -> None:
        e = HashingEmbedder(128)
        a = await e.embed_query("Paid leave policy for employees")
        assert a == await e.embed_query("paid LEAVE policy, for employees!")
        assert abs(sum(x * x for x in a) - 1.0) < 1e-9
        related = await e.embed_query("How much paid leave do employees get?")
        unrelated = await e.embed_query("Quarterly stock rotation for the warehouse")
        assert cosine(a, related) > cosine(a, unrelated)
        assert await e.embed_query("") == [0.0] * 128  # nothing to hash
        assert e.model_id == "hashing-v1-128" and e.dimensions == 128

    asyncio.run(body())


def _voyage(handler: Callable[[httpx.Request], httpx.Response]) -> VoyageEmbedder:
    return VoyageEmbedder(api_key="vk-test", transport=httpx.MockTransport(handler))


def test_voyage_embedder_request_and_batching() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"auth": request.headers["authorization"], **body})
        data = [{"index": i, "embedding": [float(i), 1.0, 0.0]} for i in range(len(body["input"]))]
        return httpx.Response(200, json={"data": list(reversed(data))})

    async def body() -> None:
        embedder = _voyage(handler)
        vectors = await embedder.embed_documents([f"doc {i}" for i in range(130)])
        assert len(vectors) == 130 and vectors[1] == [1.0, 1.0, 0.0]  # sorted by index
        assert [len(c["input"]) for c in seen] == [128, 2]  # batched
        assert seen[0]["input_type"] == "document" and seen[0]["auth"] == "Bearer vk-test"
        await embedder.embed_query("q")
        assert seen[-1]["input_type"] == "query"
        assert embedder.model_id == "voyage:voyage-3.5" and embedder.dimensions == 3

    asyncio.run(body())


@pytest.mark.parametrize(
    ("status", "body", "error", "retryable"),
    [
        (429, {}, EmbeddingUnavailableError, True),
        (503, {}, EmbeddingUnavailableError, True),
        (401, {}, EmbeddingError, False),
        (200, {"unexpected": True}, EmbeddingError, False),
        (200, {"data": []}, EmbeddingError, False),
    ],
)
def test_voyage_embedder_errors(
    status: int, body: dict[str, Any], error: type[EmbeddingError], retryable: bool
) -> None:
    embedder = _voyage(lambda _: httpx.Response(status, json=body))
    with pytest.raises(error) as info:
        asyncio.run(embedder.embed_query("q"))
    assert info.value.retryable is retryable
    assert type(info.value) is error


def test_embedder_factory() -> None:
    assert isinstance(create_embedder(MemorySettings()), HashingEmbedder)
    with pytest.raises(ConfigurationError):
        create_embedder(MemorySettings(embedding_provider="voyage"))
    voyage = create_embedder(
        MemorySettings(embedding_provider="voyage", voyage_api_key="k", embedding_model="m-1")
    )
    assert voyage.model_id == "voyage:m-1"


# --- chunking -----------------------------------------------------------------------------


def test_chunking_respects_size_overlap_and_keeps_all_words() -> None:
    paragraphs = [f"Paragraph {i}. " + "word " * (20 + i * 7) for i in range(12)]
    text = "\n\n".join(paragraphs) + "\n\n" + "x" * 900  # one unbreakable run
    chunks = chunk_text(text, max_chars=300, overlap_chars=40)
    assert len(chunks) > 3
    assert all(len(c) <= 300 for c in chunks)
    joined = " ".join(chunks)
    for i in range(12):
        assert f"Paragraph {i}." in joined
    assert chunk_text("Short text.", max_chars=300) == ["Short text."]
    with pytest.raises(ValueError):
        chunk_text("x", max_chars=10)


# --- working memory -----------------------------------------------------------------------


def test_working_memory_scoped_by_tenant_user_thread() -> None:
    async def body() -> None:
        wm = WorkingMemory(
            TemporaryStateStore(InMemoryBackend(), default_ttl_seconds=60), ttl_seconds=60
        )
        a = MemoryScope("acme", "asha")
        await wm.set(a, "th-1", "focus", {"customer": "C-1"})
        await wm.set(a, "th-1", "step", 2)
        assert await wm.get_all(a, "th-1") == {"focus": {"customer": "C-1"}, "step": 2}
        assert await wm.get_all(a, "th-2") == {}
        assert await wm.get_all(MemoryScope("acme", "ravi"), "th-1") == {}
        assert await wm.get_all(MemoryScope("other", "asha"), "th-1") == {}
        assert await wm.delete(a, "th-1", "step") is True
        assert await wm.delete(a, "th-1", "step") is False
        await wm.clear(a, "th-1")
        assert await wm.get_all(a, "th-1") == {}
        with pytest.raises(ValueError):
            await wm.set(a, "th-1", "bad key", 1)

    asyncio.run(body())


def test_working_memory_expires() -> None:
    async def body() -> None:
        wm = WorkingMemory(
            TemporaryStateStore(InMemoryBackend(), default_ttl_seconds=1), ttl_seconds=1
        )
        s = MemoryScope("acme", "asha")
        await wm.set(s, "th", "k", "v")
        await asyncio.sleep(1.1)
        assert await wm.get(s, "th", "k") is None

    asyncio.run(body())


# --- recall -------------------------------------------------------------------------------


def test_recall_gathers_all_sections_and_renders_bounded_context() -> None:
    async def body() -> None:
        m = make_manager(InMemoryMemoryStore(), recall_min_score=0.0)
        s = MemoryScope("acme", "asha")
        await m.working.set(s, "th", "focus", "leave request")
        await m.conversation.save_summary(s, "th", "Asha is planning leave in December.")
        await m.long_term.remember(s, "prefs", "language", "Asha prefers replies in Hindi.")
        await m.semantic.ingest(s, source_ref="hr", text="Paid leave is 18 days per year.")
        context = await m.recall(s, thread_id="th", query="how much paid leave can Asha take")
        assert context.working == {"focus": "leave request"}
        assert context.conversation_summary == "Asha is planning leave in December."
        assert context.facts and context.knowledge and context.errors == []

        view = context.to_prompt(10_000)
        assert view is not None and "not a source of truth" in view["note"]
        assert view["knowledge"][0]["source"] == "hr"
        small = context.to_prompt(40)
        assert small is not None
        assert len(json.dumps(small)) < len(json.dumps(view))
        assert MemoryContext().to_prompt(100) is None

    asyncio.run(body())


class _BrokenEmbedder(HashingEmbedder):
    async def embed_query(self, text: str) -> list[float]:
        raise EmbeddingUnavailableError("provider down")


def test_recall_failures_are_isolated() -> None:
    async def body() -> None:
        store = InMemoryMemoryStore()
        m = MemoryManager(
            store=store,
            vectors=store,
            embedder=_BrokenEmbedder(),
            working_store=TemporaryStateStore(InMemoryBackend(), default_ttl_seconds=60),
            prompts=None,
            settings=AppSettings(),
        )
        s = MemoryScope("acme", "asha")
        await m.working.set(s, "th", "k", "v")
        context = await m.recall(s, thread_id="th", query="anything")
        assert context.working == {"k": "v"}  # the healthy section still answered
        assert context.errors == ["EMBEDDING_UNAVAILABLE"]

    asyncio.run(body())


def test_forget_thread_removes_every_trace() -> None:
    async def body() -> None:
        from agentic_platform.memory import Episode

        m = make_manager(InMemoryMemoryStore())
        s = MemoryScope("acme", "asha")
        await m.working.set(s, "th", "k", "v")
        await m.conversation.save_summary(s, "th", "summary")
        await m.episodic.record(
            s, Episode(thread_id="th", request_id="r", outcome="completed", summary="x")
        )
        result = await m.forget_thread(s, "th")
        assert result == {"conversation_summary": 1, "episodic": 1}
        assert await m.working.get_all(s, "th") == {}
        with pytest.raises(ValueError):
            await m.forget_user(MemoryScope("acme"))

    asyncio.run(body())


# --- agent integration ------------------------------------------------------------------------

MakeAgent = Callable[..., tuple[Agent, ScriptedLLM]]


def _agent(
    settings: AppSettings, clock: ClockService, *script: object
) -> tuple[Agent, ScriptedLLM]:
    llm = ScriptedLLM(script=list(script))
    deps = build_dependencies(settings, llm=llm, clock=clock)
    return Agent(deps, checkpointer=InMemorySaver()), llm


def test_agent_recalls_memory_into_the_answer_prompt(
    settings: AppSettings, clock: ClockService
) -> None:
    agent, llm = _agent(settings, clock, understanding(intent="greet"), "Namaste!")
    memory = agent.dependencies.memory
    assert memory is not None
    asha = MemoryScope("acme", "asha")
    asyncio.run(memory.long_term.remember(asha, "prefs", "language", "Asha prefers Hindi replies."))

    # The hashing embedder is lexical: the question shares words with the fact.
    response = agent.run(
        "Hello, please use the Hindi replies Asha prefers", principal=principal("acme", "asha")
    )
    assert response.status == "completed"
    prompt = llm.calls[1].messages[-1].content
    assert "Asha prefers Hindi replies." in prompt
    assert "not a source of truth" in prompt
    assert "memory" in llm.calls[1].system  # system prompt v2 explains memory

    other, other_llm = _agent(settings, clock, understanding(intent="greet"), "Hi!")
    object.__setattr__(other.dependencies, "memory", memory)  # same store, other tenant
    other.run(
        "Hello, please use the Hindi replies Asha prefers", principal=principal("globex", "asha")
    )
    assert "Asha prefers Hindi" not in other_llm.calls[1].messages[-1].content


def test_agent_records_episodes_for_finished_turns_only(
    settings: AppSettings, clock: ClockService
) -> None:
    agent, _ = _agent(
        settings,
        clock,
        understanding(tool_calls=[("system.current_time", "{}")]),
        "It is 06:30.",
        understanding(intent="boom"),
        RuntimeError("llm exploded"),
    )
    memory = agent.dependencies.memory
    assert memory is not None
    scope = MemoryScope("acme", "asha")
    agent.run("What time is it?", thread_id="ep-1", principal=principal("acme", "asha"))
    agent.run("Explode", thread_id="ep-2", principal=principal("acme", "asha"))

    episodes = asyncio.run(memory.episodic.recent(scope))
    assert [e.thread_id for e in episodes] == ["ep-1"]  # the failed turn is not remembered
    assert episodes[0].metadata["tools"] == ["system.current_time"]
    assert episodes[0].content.startswith("test summary")


def test_memory_failure_never_fails_a_turn(settings: AppSettings, clock: ClockService) -> None:
    agent, _ = _agent(settings, clock, understanding(), "Hello.")
    memory = agent.dependencies.memory
    assert memory is not None
    object.__setattr__(memory, "_embedder", _BrokenEmbedder())
    memory.long_term._embedder = _BrokenEmbedder()  # type: ignore[attr-defined]
    memory.episodic._embedder = _BrokenEmbedder()  # type: ignore[attr-defined]
    assert agent.run("hi").status == "completed"


def test_memory_can_be_disabled(settings: AppSettings, clock: ClockService) -> None:
    disabled = settings.model_copy(update={"memory": MemorySettings(enabled=False)})
    agent, llm = _agent(disabled, clock, understanding(), "Hello.")
    assert agent.dependencies.memory is None
    assert agent.run("hi").status == "completed"
    assert '"memory"' not in llm.calls[1].messages[-1].content


def test_conversation_and_checkpoint_memory_read_the_checkpointer(
    settings: AppSettings, clock: ClockService
) -> None:
    agent, _ = _agent(settings, clock, understanding(), "Hello there.")
    memory = agent.dependencies.memory
    assert memory is not None
    agent.run("Hi", thread_id="conv-1")
    key = f"{LOCAL_TENANT}/conv-1"  # checkpoints are stored under <tenant>/<thread> (Phase 5)
    authorized = agent.dependencies.authorized_memory()
    assert authorized is not None
    local = agent.dependencies.security.local_identity.principal()

    async def body() -> None:
        turns = await memory.conversation.history(key)
        assert [(t.role, t.content) for t in turns] == [
            ("user", "Hi"),
            ("assistant", "Hello there."),
        ]
        # The owner reads the same history by its public id through AuthorizedMemory.
        assert await authorized.history(local, "conv-1") == turns
        latest = await memory.checkpoints.latest(key)
        assert latest is not None and "messages" in latest.channels
        assert len(await memory.checkpoints.history(key)) > 1
        await memory.checkpoints.delete(key)
        assert await memory.conversation.history(key) == []

    asyncio.run(body())
