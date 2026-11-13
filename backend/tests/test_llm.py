from typing import TypeVar

import pytest
from pydantic import BaseModel

from app.clients import llm
from app.clients.llm import LLMUnavailableError

T = TypeVar("T")


class FakeOutput(BaseModel):
    intent: str
    budget: int | None = None


def make_payload(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


async def test_structured_call_success(monkeypatch):
    async def fake_post(messages, temperature=0.3):
        return make_payload('{"intent": "itinerary_request", "budget": 300}')

    monkeypatch.setattr(llm, "_post_chat", fake_post)
    result = await llm.structured_call("sys", "user", FakeOutput)
    assert result.intent == "itinerary_request"
    assert result.budget == 300


async def test_structured_call_retry_then_success(monkeypatch):
    calls = []

    async def fake_post(messages, temperature=0.3):
        calls.append(messages)
        if len(calls) == 1:
            return make_payload("这不是 JSON")
        return make_payload('{"intent": "knowledge_qa"}')

    monkeypatch.setattr(llm, "_post_chat", fake_post)
    result = await llm.structured_call("sys", "user", FakeOutput)
    assert result.intent == "knowledge_qa"
    assert len(calls) == 2


async def test_structured_call_fails_after_retries(monkeypatch):
    async def fake_post(messages, temperature=0.3):
        return make_payload("永远不合法")

    monkeypatch.setattr(llm, "_post_chat", fake_post)
    with pytest.raises(LLMUnavailableError):
        await llm.structured_call("sys", "user", FakeOutput, max_attempts=2)


async def test_structured_call_codefence_json(monkeypatch):
    async def fake_post(messages, temperature=0.3):
        return make_payload("```json\n{\"intent\": \"invalid_input\"}\n```")

    monkeypatch.setattr(llm, "_post_chat", fake_post)
    result = await llm.structured_call("sys", "user", FakeOutput)
    assert result.intent == "invalid_input"


async def test_text_call_success(monkeypatch):
    async def fake_post(messages, temperature=0.3):
        assert messages[0]["role"] == "system"
        assert messages[-1]["role"] == "user"
        return make_payload("你好")

    monkeypatch.setattr(llm, "_post_chat", fake_post)
    text = await llm.text_call("sys", "user")
    assert text == "你好"