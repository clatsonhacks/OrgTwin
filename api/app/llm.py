"""LLM access through Vultr Serverless Inference (OpenAI-compatible).

With no VULTR_INFERENCE_API_KEY, callers' deterministic `fallback` is used and the
result is flagged as mocked so the UI can say so.
"""
import json
import re
from typing import Any, Callable

import httpx

from .config import settings

_model_cache: str | None = None


class LLMResult:
    def __init__(self, text: str, mocked: bool, model: str):
        self.text, self.mocked, self.model = text, mocked, model


async def _model(client: httpx.AsyncClient) -> str:
    global _model_cache
    if settings.inference_model:
        return settings.inference_model
    if _model_cache:
        return _model_cache
    r = await client.get(f"{settings.inference_base_url}/models", headers=_headers())
    r.raise_for_status()
    models = [m["id"] for m in r.json().get("data", [])]
    if not models:
        raise RuntimeError("Vultr Serverless Inference returned no models; set VULTR_INFERENCE_MODEL")
    _model_cache = models[0]
    return _model_cache


_VISION_HINTS = ("vl", "vision", "llava", "pixtral", "gemma-3", "llama-4", "qvq", "internvl", "minicpm-v")
_vision_cache: str | None | bool = False  # False = not looked up yet


async def vision_model() -> str | None:
    """The configured vision model, else the first model on Vultr whose id looks vision-capable."""
    global _vision_cache
    if settings.vision_model:
        return settings.vision_model
    if not settings.inference_api_key:
        return None
    if _vision_cache is not False:
        return _vision_cache  # type: ignore[return-value]
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{settings.inference_base_url}/models", headers=_headers())
            r.raise_for_status()
            ids = [m["id"] for m in r.json().get("data", [])]
        _vision_cache = next((i for i in ids if any(h in i.lower() for h in _VISION_HINTS)), None)
    except Exception:
        _vision_cache = None
    return _vision_cache  # type: ignore[return-value]


async def describe_images(prompt: str, images: list[str], max_tokens: int = 1500) -> LLMResult | None:
    """Send data-URL images to the vision model. Returns None when no vision model is available."""
    model = await vision_model()
    if not model:
        return None
    content: list[dict] = [{"type": "text", "text": prompt}]
    content += [{"type": "image_url", "image_url": {"url": img}} for img in images]
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(f"{settings.inference_base_url}/chat/completions", headers=_headers(),
                              json={"model": model, "temperature": 0.2, "max_tokens": max_tokens, **_reasoning(),
                                    "messages": [{"role": "user", "content": content}]})
        r.raise_for_status()
        return LLMResult(r.json()["choices"][0]["message"]["content"], mocked=False, model=model)


def _reasoning() -> dict:
    return {"reasoning_effort": settings.reasoning_effort} if settings.reasoning_effort else {}


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.inference_api_key}"}


async def complete(system: str, user: str, fallback: Callable[[], str],
                   temperature: float = 0.4, max_tokens: int = 4000) -> LLMResult:
    if not settings.inference_api_key:
        return LLMResult(fallback(), mocked=True, model="mock")
    async with httpx.AsyncClient(timeout=90) as client:
        model = await _model(client)
        r = await client.post(
            f"{settings.inference_base_url}/chat/completions",
            headers=_headers(),
            json={"model": model, "temperature": temperature, "max_tokens": max_tokens, **_reasoning(),
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
        )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        return LLMResult(text, mocked=False, model=model)


def extract_json(text: str) -> Any:
    """Parse the first JSON object/array in a model reply (handles ```json fences, <think> blocks)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if m:
        text = m.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i != -1], default=-1)
    if start == -1:
        raise ValueError("no JSON in model output")
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return _loose(text[start:])


def _loose(text: str) -> Any:
    # trim trailing prose after the last closing brace/bracket
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[: end + 1])
