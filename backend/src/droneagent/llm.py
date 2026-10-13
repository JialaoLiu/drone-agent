"""模型调用：OpenAI 兼容接口（DeepSeek 默认，OpenAI 可选），统一返回解析后的 JSON 与用量。

文字与看图共用一个入口；每次调用记录 Token、缓存命中、耗时与费用，交给调用方写入执行轨迹。
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from . import config, pricing


class ModelUnavailable(RuntimeError):
    pass


@dataclass
class Usage:
    model: str
    prompt_tokens: int
    completion_tokens: int
    cache_hit_tokens: int
    latency_ms: int
    cost_yuan: float | None
    images: int = 0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _endpoint(provider: str) -> tuple[str, str]:
    if provider == "openai":
        if not config.OPENAI_API_KEY:
            raise ModelUnavailable("没有配置 OPENAI_API_KEY")
        return config.OPENAI_BASE_URL.rstrip("/"), config.OPENAI_API_KEY
    if not config.LLM_API_KEY:
        raise ModelUnavailable("没有配置 LLM_API_KEY")
    return config.LLM_BASE_URL.rstrip("/"), config.LLM_API_KEY


def image_part(jpeg: bytes) -> dict:
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}


def chat_json(system: str, user: str, images: list[bytes] | None = None, model: str | None = None,
              provider: str = "deepseek", temperature: float = 0.0, timeout: float = 90.0) -> tuple[dict, Usage]:
    """调用一次模型并解析 JSON。超时或 5xx 重试一次；返回内容不是 JSON 时再重试一次。"""
    base, key = _endpoint(provider)
    model = model or config.LLM_MODEL
    content: list | str = user
    if images:
        content = [{"type": "text", "text": user}] + [image_part(i) for i in images]
    body = {"model": model, "temperature": temperature, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
    if images and provider == "deepseek":
        body.pop("response_format")   # 视觉实验模型暂不保证支持 JSON 模式，靠提示词约束
    last: Exception | None = None
    for attempt in range(3):
        started = time.monotonic()
        try:
            r = httpx.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=timeout)
            if r.status_code >= 500 or r.status_code == 429:
                last = httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            text = data["choices"][0]["message"]["content"]
            parsed = _parse(text)
            usage = data.get("usage") or {}
            hit = usage.get("prompt_cache_hit_tokens", (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)) or 0
            prompt = usage.get("prompt_tokens", 0)
            served = data.get("model") or model
            cost, _ = pricing.cost(served, hit, prompt - hit, usage.get("completion_tokens", 0), datetime.now(timezone.utc))
            return parsed, Usage(served, prompt, usage.get("completion_tokens", 0), hit,
                                 round((time.monotonic() - started) * 1000), cost, len(images or []))
        except (httpx.TimeoutException, json.JSONDecodeError, ValueError) as error:
            last = error
        except (httpx.HTTPError, KeyError) as error:
            raise ModelUnavailable(f"模型接口调用失败：{type(error).__name__} {error}") from error
    raise ModelUnavailable(f"模型接口调用失败：{type(last).__name__} {last}")


def _parse(text: str) -> dict:
    """解析模型输出的 JSON；容忍代码块包裹。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型输出中没有 JSON")
    return json.loads(text[start:end + 1])
