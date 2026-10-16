"""模型调用：OpenAI 兼容接口（DeepSeek 默认，OpenAI 可选），统一返回解析后的 JSON 与用量。

文字与看图共用一个入口；每次调用记录 Token、缓存命中、耗时与费用，交给调用方写入执行轨迹。
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

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


# 本进程内的累计用量（评测用来按 Token 比较不同方案，不受高峰 / 闲时单价影响）
STATS = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cache_hit_tokens": 0, "reasoning_tokens": 0, "cached_answers": 0}
_stats_lock = threading.Lock()


def reset_stats() -> None:
    with _stats_lock:
        for k in STATS:
            STATS[k] = 0


def _cache_path(body: dict) -> "Path":
    key = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return config.DATA_DIR / "cache" / "llm" / key[:2] / f"{key}.json"


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
              provider: str = "deepseek", temperature: float = 0.0, timeout: float = 90.0,
              thinking: bool | None = None, max_tokens: int | None = None) -> tuple[dict, Usage]:
    """调用一次模型并解析 JSON。超时或 5xx 重试一次；返回内容不是 JSON 时再重试一次。

    thinking=False 时关闭模型的推理过程（DeepSeek 的 thinking 参数）：推理按输出 Token 计费，而我们只需要结论 JSON。
    max_tokens 只在关闭推理时作为安全上限使用：开着推理时，上限会把推理也算进去，推理一长 JSON 就被截断。
    环境变量 LLM_CACHE=1 时（评测用），完全相同的请求直接返回缓存的结论，不再调用模型。"""
    base, key = _endpoint(provider)
    model = model or config.LLM_MODEL
    content: list | str = user
    if images:
        content = [{"type": "text", "text": user}] + [image_part(i) for i in images]
    body = {"model": model, "temperature": temperature, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
    if images and provider == "deepseek":
        body.pop("response_format")   # 视觉实验模型暂不保证支持 JSON 模式，靠提示词约束
    if thinking is False and provider == "deepseek":
        body["thinking"] = {"type": "disabled"}
        if max_tokens:
            body["max_tokens"] = max_tokens
    cache = _cache_path(body) if config.env("LLM_CACHE", "0") == "1" else None
    if cache and cache.exists():
        hit = json.loads(cache.read_text(encoding="utf-8"))
        with _stats_lock:
            STATS["cached_answers"] += 1
        return hit["parsed"], Usage(hit["model"], 0, 0, 0, 0, 0.0, len(images or []))
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
            with _stats_lock:
                STATS["calls"] += 1
                STATS["prompt_tokens"] += prompt
                STATS["completion_tokens"] += usage.get("completion_tokens", 0)
                STATS["cache_hit_tokens"] += hit
                STATS["reasoning_tokens"] += (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps({"parsed": parsed, "model": served}, ensure_ascii=False), encoding="utf-8")
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
