"""模型调用封装：关闭推理的请求参数、按 Token 统计、相同请求的缓存（替身接口，不调用模型）。"""
import json

import httpx

from droneagent import config, llm


class FakeResponse:
    status_code = 200

    def __init__(self, body):
        self._body = body
        self.request = None

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


def _fake(monkeypatch, sent):
    def post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        return FakeResponse({"model": "deepseek-flash", "choices": [{"message": {"content": '{"status": "ok"}'}}],
                             "usage": {"prompt_tokens": 600, "completion_tokens": 40, "prompt_cache_hit_tokens": 200,
                                       "completion_tokens_details": {"reasoning_tokens": 0}}})
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(config, "LLM_API_KEY", "test")


def test_thinking_disabled_sets_parameter_and_cap(monkeypatch):
    sent = []
    _fake(monkeypatch, sent)
    llm.reset_stats()
    llm.chat_json("系统", "用户", [b"jpeg"], thinking=False, max_tokens=300)
    llm.chat_json("系统", "用户", [b"jpeg"], thinking=True, max_tokens=300)
    assert sent[0]["thinking"] == {"type": "disabled"} and sent[0]["max_tokens"] == 300
    assert "thinking" not in sent[1] and "max_tokens" not in sent[1]        # 开着推理时不设上限，避免截断 JSON
    assert llm.STATS["calls"] == 2 and llm.STATS["completion_tokens"] == 80 and llm.STATS["cache_hit_tokens"] == 400


def test_identical_request_uses_cache(monkeypatch):
    sent = []
    _fake(monkeypatch, sent)
    monkeypatch.setenv("LLM_CACHE", "1")
    llm.reset_stats()
    a, _ = llm.chat_json("系统", "用户", [b"jpeg"], thinking=False)
    b, usage = llm.chat_json("系统", "用户", [b"jpeg"], thinking=False)
    c, _ = llm.chat_json("系统", "另一个问题", [b"jpeg"], thinking=False)
    assert a == b == {"status": "ok"} and usage.cost_yuan == 0.0
    assert len(sent) == 2 and llm.STATS["cached_answers"] == 1


def test_second_look_is_not_served_from_first_answer(monkeypatch):
    sent = []
    _fake(monkeypatch, sent)
    monkeypatch.setenv("LLM_CACHE", "1")
    llm.chat_json("系统", "同一张图", [b"jpeg"], thinking=False, attempt=0)
    llm.chat_json("系统", "同一张图", [b"jpeg"], thinking=False, attempt=1)
    assert len(sent) == 2
