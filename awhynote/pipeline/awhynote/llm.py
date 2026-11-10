"""LLM access over plain HTTP. Providers: openai (any OpenAI-compatible API), anthropic, mock."""
from __future__ import annotations

import json
import re
import time

import requests

from .config import Config


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("model did not return JSON")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"model returned invalid JSON: {e}") from e


class LLM:
    """profile: 'default' | 'research' | 'check'. LLM_<PROFILE>_PROVIDER/MODEL/API_KEY/BASE_URL override the defaults."""

    def __init__(self, cfg: Config, profile: str = "default"):
        raw = cfg.raw or {}
        pre = "" if profile == "default" else f"LLM_{profile.upper()}_"

        self.provider = (raw.get(pre + "PROVIDER") or cfg.llm_provider).lower()
        self.api_key = raw.get(pre + "API_KEY") or cfg.llm_api_key
        self.model = raw.get(pre + "MODEL") or (cfg.llm_check_model if profile == "check" and cfg.llm_check_model else cfg.llm_model)
        self.base_url = (raw.get(pre + "BASE_URL") or cfg.llm_base_url).rstrip("/")
        self.profile = profile

    def complete(self, system: str, user: str, *, task: str = "", mock_payload: dict | None = None, json_mode: bool = True) -> str:
        if self.provider == "mock":
            from .mock import mock_llm

            return json.dumps(mock_llm(task, mock_payload or {}), ensure_ascii=False)
        if not self.api_key or not self.model:
            raise LLMError("LLM_API_KEY and LLM_MODEL must be set (or use LLM_PROVIDER=mock)")
        if self.provider == "anthropic":
            return self._anthropic(system, user)
        if self.provider in ("openai", "openai-compatible"):
            return self._openai(system, user, json_mode)
        raise LLMError(f"unknown LLM provider: {self.provider}")

    def complete_json(self, system: str, user: str, *, task: str = "", mock_payload: dict | None = None) -> dict:
        text = self.complete(system, user, task=task, mock_payload=mock_payload)
        try:
            return extract_json(text)
        except LLMError:
            repair = user + "\n\nYour previous reply was not valid JSON. Reply again with ONLY the JSON object."
            return extract_json(self.complete(system, repair, task=task, mock_payload=mock_payload))

    # -- providers
    def _post(self, url: str, headers: dict, body: dict) -> dict:
        last = None
        for attempt in range(3):
            try:
                r = requests.post(url, headers=headers, json=body, timeout=180)
                if r.status_code in (429, 500, 502, 503, 529):
                    last = LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
                    time.sleep(2 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:400]}")
                return r.json()
            except requests.RequestException as e:
                last = LLMError(str(e))
                time.sleep(2 * (attempt + 1))
        raise last or LLMError("request failed")

    def _openai(self, system: str, user: str, json_mode: bool) -> str:
        body = {"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        data = self._post(f"{self.base_url}/chat/completions", {"Authorization": f"Bearer {self.api_key}"}, body)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"unexpected response: {str(data)[:300]}") from e

    def _anthropic(self, system: str, user: str) -> str:
        base = self.base_url if "anthropic" in self.base_url else "https://api.anthropic.com/v1"
        body = {"model": self.model, "max_tokens": 8000, "system": system, "messages": [{"role": "user", "content": user}]}
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        data = self._post(f"{base}/messages", headers, body)
        try:
            return "".join(b.get("text", "") for b in data["content"])
        except (KeyError, TypeError) as e:
            raise LLMError(f"unexpected response: {str(data)[:300]}") from e
