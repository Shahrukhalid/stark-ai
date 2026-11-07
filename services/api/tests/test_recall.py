"""Episodic memory: Vision and Wanda recall their own past conversations, by meaning when a Gemini key is set."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from stark import embeddings
from stark.core.memory import _tokens
from stark.tools import memory_tools


@pytest.fixture
def kernel(settings):
    from stark.main import build_kernel

    return build_kernel(settings, {})


def test_words_in_any_script_are_searchable():
    assert "لانچ" in _tokens("ہمارا لانچ پلان")
    assert _tokens("Launch-week plan") == ["launch-week", "plan"]


def _kernel_with_history(kernel):
    from stark.core.db import Conversation, Turn

    with kernel.db.session() as s:
        for agent, pairs in (("vision", [("What did we decide about the launch date?", "We picked Friday the 14th."),
                                         ("How is Instagram doing?", "Likes are up 12% this week.")]),
                             ("wanda", [("Draft a launch caption", "Here is a caption about the launch.")])):
            conv = Conversation(agent_id=agent)
            s.add(conv)
            s.flush()
            for q, a in pairs:
                s.add(Turn(conversation_id=conv.id, role="user", agent_id=agent, content=q))
                s.add(Turn(conversation_id=conv.id, role="assistant", agent_id=agent, content=a))


async def test_recall_by_words_only_sees_own_conversations(kernel):
    _kernel_with_history(kernel)
    settings = SimpleNamespace(gemini_api_key="")
    ctx = SimpleNamespace(kernel=kernel, settings=settings, agent=SimpleNamespace(id="vision"))
    r = await memory_tools.MemoryRecall().run(memory_tools.RecallInput(query="launch date"), ctx)
    texts = [e["text"] for e in r.data["episodes"]]
    assert r.ok and "by words" in r.summary and texts and "Friday the 14th" in texts[0]
    assert not any("caption" in t for t in texts)  # Wanda's conversation is never visible to Vision


async def test_recall_by_meaning_with_gemini(kernel, monkeypatch):
    _kernel_with_history(kernel)
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [
                {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]}]})
        body = json.loads(req.content)
        calls.append(len(body["requests"]))
        vecs = [[1.0, 0.0] if "launch" in r["content"]["parts"][0]["text"].lower() or "when" in r["content"]["parts"][0]["text"].lower()
                else [0.0, 1.0] for r in body["requests"]]
        return httpx.Response(200, json={"embeddings": [{"values": v} for v in vecs]})

    monkeypatch.setattr(memory_tools, "_EMBEDDERS", {"k": embeddings.Embedder("k", httpx.MockTransport(handler))})
    ctx = SimpleNamespace(kernel=kernel, settings=SimpleNamespace(gemini_api_key="k"), agent=SimpleNamespace(id="vision"))
    r = await memory_tools.MemoryRecall().run(memory_tools.RecallInput(query="when do we go live?"), ctx)
    assert "by meaning" in r.summary and "Friday the 14th" in r.data["episodes"][0]["text"]
    await memory_tools.MemoryRecall().run(memory_tools.RecallInput(query="when do we go live?"), ctx)
    assert calls == [2, 1, 1]  # the two exchanges are embedded once, then only the questions


async def test_recall_respects_memory_off(kernel):
    kernel.memory.set_enabled(False)
    ctx = SimpleNamespace(kernel=kernel, settings=SimpleNamespace(gemini_api_key=""), agent=SimpleNamespace(id="vision"))
    r = await memory_tools.MemoryRecall().run(memory_tools.RecallInput(query="launch"), ctx)
    assert r.data["disabled"]
