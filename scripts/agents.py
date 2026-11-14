"""Claude API の薄いクライアントと料金計算(標準ライブラリのみ)。"""
import json
import re
import time
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"


class AgentError(Exception):
    pass


def web_search_tool(max_uses, domains=None):
    tool = {"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}
    if domains:
        tool["allowed_domains"] = domains
    return tool


def call(api_key, model, system, prompt, tools=None, max_tokens=4000):
    """1エージェント分の呼び出し。pause_turn を処理し、テキストと使用量を返す。"""
    messages = [{"role": "user", "content": prompt}]
    usage = {"in": 0, "out": 0, "cache_read": 0, "searches": 0, "requests": []}
    text = ""
    for _ in range(6):
        body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        if tools:
            body["tools"] = tools
        req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), headers={
            "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    resp = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 529) and attempt < 2:
                    time.sleep(10 * (attempt + 1))
                    continue
                raise AgentError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
            except urllib.error.URLError as e:
                raise AgentError(str(e))
        u = resp.get("usage", {})
        req_in = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
        usage["in"] += req_in
        usage["out"] += u.get("output_tokens", 0)
        usage["cache_read"] += u.get("cache_read_input_tokens", 0)
        usage["searches"] += (u.get("server_tool_use") or {}).get("web_search_requests", 0)
        usage["requests"].append({"in": req_in, "out": u.get("output_tokens", 0),
                                  "cache_read": u.get("cache_read_input_tokens", 0)})
        text += "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        if resp.get("stop_reason") != "pause_turn":
            break
        messages.append({"role": "assistant", "content": resp["content"]})
    return text, usage


def parse_json(text):
    m = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    raw = m[-1] if m else text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise AgentError(f"JSONを読めませんでした: {e}")


def cost_usd(model, usage, pricing):
    """モデル別の単価表(site.json)で概算する。Haiku 5.5 は10万トークン超のリクエストが5倍。"""
    p = pricing["models"].get(model)
    if not p:
        return None
    total = 0.0
    for r in usage["requests"]:
        mult = p.get("long_mult", 1) if r["in"] + r["cache_read"] > p.get("long_threshold", 10**12) else 1
        total += (r["in"] * p["in"] + r["out"] * p["out"] + r["cache_read"] * p.get("cache_read", p["in"] * 0.1)) * mult / 1e6
    total += usage["searches"] * pricing["web_search_per_1000"] / 1000
    return round(total, 5)
