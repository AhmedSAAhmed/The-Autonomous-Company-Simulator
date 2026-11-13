"""LLM judgement for the agents, with a strict budget and a rule fallback.

Providers are tried in order (Groq first). Every call is rate limited so a public
demo cannot burn through a free tier; when no call is allowed or a call fails the
agent simply uses its rule-based answer.
"""
import json
import os
import time

import httpx

PROVIDERS = [
    ("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "GROQ_MODEL", "openai/gpt-oss-20b"),
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY",
     "GEMINI_MODEL", "gemini-2.5-flash"),
    ("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "OPENROUTER_MODEL",
     "openai/gpt-oss-20b"),
]


class LLM:
    def __init__(self, min_interval=2.2, timeout=6.0):
        self.providers = [
            {"name": n, "url": u, "key": os.environ[k], "model": os.environ.get(m, d), "fails": 0,
             "retry_at": 0.0}
            for n, u, k, m, d in PROVIDERS if os.environ.get(k)
        ]
        self.min_interval, self.timeout = min_interval, timeout
        self.last_call = 0.0
        self.calls = self.fallbacks = 0
        self.last_provider = None
        self.client = httpx.AsyncClient(timeout=timeout) if self.providers else None

    @property
    def enabled(self):
        return bool(self.providers)

    def stats(self):
        return {"enabled": self.enabled, "calls": self.calls, "fallbacks": self.fallbacks,
                "provider": self.last_provider or (self.providers[0]["name"] if self.providers else None)}

    async def decide(self, system, task, keys):
        """Ask for a JSON object holding `keys`. Returns None when the caller should use rules."""
        now = time.monotonic()
        if not self.enabled or now - self.last_call < self.min_interval:
            return None
        self.last_call = now
        for p in self.providers:
            if p["retry_at"] > now:
                continue
            try:
                r = await self.client.post(
                    p["url"] + "/chat/completions",
                    headers={"Authorization": "Bearer " + p["key"]},
                    json={"model": p["model"], "temperature": 0.2, "max_tokens": 400,
                          "response_format": {"type": "json_object"},
                          "messages": [{"role": "system", "content": system},
                                       {"role": "user", "content": task}]},
                )
                r.raise_for_status()
                out = json.loads(r.json()["choices"][0]["message"]["content"])
                if not all(k in out for k in keys):
                    raise ValueError("missing keys")
                p["fails"] = 0
                self.calls += 1
                self.last_provider = p["name"]
                return out
            except Exception:
                p["fails"] += 1
                if p["fails"] >= 3:
                    p["retry_at"], p["fails"] = now + 60, 0
        self.fallbacks += 1
        return None
