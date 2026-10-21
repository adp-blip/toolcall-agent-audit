"""Groq chat client with tool calling (OpenAI-compatible API). Stdlib only.

Keys come from GROQ_API_KEYS (comma separated) or GROQ_API_KEY, via env or a .env
file in this repo or its parent. Multiple keys get rotated when one is rate limited.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://api.groq.com/openai/v1/chat/completions"
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _load_dotenv():
    root = Path(__file__).resolve().parent.parent
    for env in (root / ".env", root.parent / ".env"):
        if env.exists():
            for line in env.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    if v.strip():
                        os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_dotenv()
_key_idx = 0


def _keys():
    raw = os.environ.get("GROQ_API_KEYS") or os.environ.get("GROQ_API_KEY") or ""
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise SystemExit("set GROQ_API_KEY or GROQ_API_KEYS (env var or .env file)")
    return keys


def complete(model, messages, tools=None, temperature=0.0, max_tokens=2048, timeout=120):
    """Returns the raw assistant message dict (may contain tool_calls)."""
    global _key_idx
    model = model.removeprefix("groq:")
    payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if model.startswith("openai/gpt-oss"):
        payload["reasoning_effort"] = "low"  # keeps token use inside the free tier limits
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    keys = _keys()
    deadline = time.time() + 900  # rate limits are shared across runs, so be patient
    for attempt in range(10_000):
        key = keys[_key_idx % len(keys)]
        req = urllib.request.Request(URL, data=json.dumps(payload).encode(), headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {key}",
            "User-Agent": "toolaudit/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                msg = json.load(resp)["choices"][0]["message"]
            if msg.get("content"):
                msg["content"] = THINK_RE.sub("", msg["content"]).strip()
            return msg
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="ignore")
            # Groq returns 400 tool_use_failed when the model emits a malformed tool call.
            # That's a model failure we want to record, not crash on.
            if e.code == 400 and "tool_use_failed" in body:
                return {"role": "assistant", "content": "", "_error": "malformed_tool_call"}
            if e.code in (429, 500, 502, 503) and time.time() < deadline:
                _key_idx += 1  # spread load across keys (only helps if they are on different accounts)
                time.sleep(max(float(e.headers.get("retry-after") or 0), min(30, 2 ** min(attempt, 5))) + 1)
                continue
            raise RuntimeError(f"groq {e.code}: {body[:300]}") from e
    raise RuntimeError("groq: out of retries")
