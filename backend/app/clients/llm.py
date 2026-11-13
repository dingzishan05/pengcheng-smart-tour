import asyncio
import json
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.clients.circuit_breaker import llm_breaker
from app.core.config import settings
from app.core.errors import BizError, ErrorCode
from app.core.logging import get_logger
from app.core.metrics import record_llm_call

logger = get_logger("app.llm")

T = TypeVar("T", bound=BaseModel)

_semaphore: asyncio.Semaphore | None = None
_queue_wait_limit = 50
_queue_waiting = 0


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.llm_max_concurrency)
    return _semaphore


class LLMUnavailableError(BizError):
    def __init__(self, message: str = "LLM 服务不可用") -> None:
        super().__init__(ErrorCode.LLM_UNAVAILABLE, message)


def _extract_content(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        raise LLMUnavailableError("LLM 返回缺少 choices")
    content = choices[0].get("message", {}).get("content")
    if content is None:
        raise LLMUnavailableError("LLM 返回缺少 content")
    return content


def _parse_json_block(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败: {exc}") from exc


async def _post_chat(messages: list[dict], temperature: float = 0.3) -> dict:
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    body = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": temperature,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
        record_llm_call(is_error=False)
        return payload
    except httpx.TimeoutException as exc:
        record_llm_call(is_error=True)
        raise LLMUnavailableError("LLM 请求超时") from exc
    except httpx.HTTPError as exc:
        record_llm_call(is_error=True)
        raise LLMUnavailableError(f"LLM 请求失败: {exc.__class__.__name__}") from exc


class EmbeddingUnavailableError(BizError):
    def __init__(self, message: str = "向量服务不可用") -> None:
        super().__init__(ErrorCode.LLM_UNAVAILABLE, message)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not settings.embedding_model:
        raise EmbeddingUnavailableError("未配置 EMBEDDING_MODEL")
    if not llm_breaker.allow_request():
        raise EmbeddingUnavailableError("向量服务熔断中")
    url = f"{settings.llm_base_url.rstrip('/')}/embeddings"
    body = {"model": settings.embedding_model, "input": texts}
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
        llm_breaker.record_success()
    except httpx.TimeoutException as exc:
        llm_breaker.record_failure()
        raise EmbeddingUnavailableError("向量服务请求超时") from exc
    except (httpx.HTTPError, ValueError) as exc:
        llm_breaker.record_failure()
        raise EmbeddingUnavailableError(f"向量服务请求失败: {exc.__class__.__name__}") from exc
    data = payload.get("data") or []
    if len(data) != len(texts):
        raise EmbeddingUnavailableError("向量服务返回数量不匹配")
    ordered = sorted(data, key=lambda item: item.get("index", 0))
    embeddings = [item.get("embedding") for item in ordered]
    if any(e is None for e in embeddings):
        raise EmbeddingUnavailableError("向量服务返回缺少 embedding")
    return embeddings


async def text_call(
    system_prompt: str,
    user_prompt: str,
    context: str | None = None,
    temperature: float = 0.3,
) -> str:
    global _queue_waiting
    if not llm_breaker.allow_request():
        raise LLMUnavailableError("LLM 熔断中")
    if _queue_waiting >= _queue_wait_limit:
        raise LLMUnavailableError("LLM 并发队列已满")
    _queue_waiting += 1
    try:
        messages = [{"role": "system", "content": system_prompt}]
        if context:
            messages.append({"role": "system", "content": f"参考资料：\n{context}"})
        messages.append({"role": "user", "content": user_prompt})
        async with _get_semaphore():
            try:
                payload = await _post_chat(messages, temperature)
                llm_breaker.record_success()
                return _extract_content(payload)
            except Exception:
                llm_breaker.record_failure()
                raise
    finally:
        _queue_waiting -= 1


async def structured_call(
    system_prompt: str,
    user_prompt: str,
    schema_model: type[T],
    max_attempts: int = 2,
) -> T:
    schema_json = json.dumps(schema_model.model_json_schema(), ensure_ascii=False)
    instruction = (
        f"{system_prompt}\n\n你必须输出一个 JSON 对象，严格符合以下 JSON Schema（不要输出任何其他内容）：\n{schema_json}"
    )
    last_error: str = ""
    messages: list[dict] = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": user_prompt},
    ]
    for attempt in range(1, max_attempts + 1):
        if not llm_breaker.allow_request():
            raise LLMUnavailableError("LLM 熔断中")
        try:
            async with _get_semaphore():
                payload = await _post_chat(messages, temperature=0.1)
            llm_breaker.record_success()
        except LLMUnavailableError:
            llm_breaker.record_failure()
            raise
        raw = _extract_content(payload)
        try:
            data = _parse_json_block(raw)
            return schema_model.model_validate(data)
        except (ValueError, ValidationError) as exc:
            last_error = str(exc)[:500]
            logger.warning(
                "structured output validation failed",
                extra={"kind": "algorithm", "attempt": attempt, "error": last_error},
            )
            if attempt < max_attempts:
                messages = messages[:2]
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": f"你输出的 JSON 不符合要求，错误信息：{last_error}。请重新输出，仅输出符合 Schema 的 JSON。",
                    }
                )
    raise LLMUnavailableError(f"LLM 结构化输出校验失败: {last_error}")