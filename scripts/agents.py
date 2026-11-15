"""OpenAI Responses API と使用量の集計。Python の標準ライブラリのみ。"""
import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, urlsplit

API_URL = "https://api.openai.com/v1/responses"


class AgentError(Exception):
    def __init__(self, message, usage=None, unmetered=False):
        super().__init__(message)
        self.usage = usage
        self.unmetered = unmetered


def url_key(url):
    """公式の HTTPS URL を比較するキー。フラグメントや追跡パラメーターは無視する。"""
    if not isinstance(url, str) or len(url) > 2000 or re.search(r"[\s\x00-\x1f<>\\]", url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if (parts.scheme != "https" or parts.netloc.lower() not in (host, host + ":443")
                or not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host)):
            return None
    except ValueError:
        return None
    query = tuple(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                         if not k.lower().startswith("utm_")))
    return host, parts.path.rstrip("/"), query


def _send(req):
    messages = {
        "invalid_api_key": "OPENAI_API_KEY の認証に失敗",
        "insufficient_quota": "OpenAI API の残高・組織の利用枠を確認",
        "project_spend_limit_exceeded": "OpenAI プロジェクトの支出上限に到達",
        "organization_spend_limit_exceeded": "OpenAI 組織の支出上限に到達",
        "model_not_found": "プロジェクトのモデル利用権限を確認",
        "rate_limit_exceeded": "OpenAI API のレート上限に到達",
    }
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            try:
                info = json.loads(error.read()).get("error", {})
                code = info.get("code")
            except (ValueError, AttributeError):
                code = None
            if error.code == 429 and code == "rate_limit_exceeded" and attempt < 2:
                try:
                    delay = float(error.headers.get("Retry-After", 0))
                except (TypeError, ValueError):
                    delay = 0
                time.sleep(min(20, max(2 ** (attempt + 1), delay)))
                continue
            hint = messages.get(code, "Actions の入力・OpenAI API の状態を確認") if isinstance(code, str) else ""
            raise AgentError(f"OpenAI HTTP {error.code} {hint}".strip(), unmetered=error.code >= 500) from None
        except (OSError, ValueError):
            # 受理されたか不明な通信エラーを自動で再送すると、二重課金になり得る。
            raise AgentError("OpenAI API の応答を取得できませんでした", unmetered=True) from None


def call(api_key, model, system, prompt, schema, max_searches, domains=None, max_tokens=3000):
    """構造化された結果と、検索で実際に返された URL・使用量を返す。"""
    search = {"type": "web_search", "search_context_size": "low", "external_web_access": True}
    if domains:
        search["filters"] = {"allowed_domains": domains}
    body = {
        "model": model, "instructions": system, "input": prompt, "store": False,
        "reasoning": {"effort": "low"}, "max_output_tokens": max_tokens,
        "tools": [search], "tool_choice": {"type": "web_search"}, "max_tool_calls": max_searches,
        "include": ["web_search_call.action.sources"],
        "text": {"format": {"type": "json_schema", "name": "price_watch_result",
                            "schema": schema, "strict": True}},
    }
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
    })
    response = _send(req)
    if not isinstance(response, dict):
        raise AgentError("OpenAI API の応答形式が不正", unmetered=True)
    output = response.get("output") or []
    searches = [item for item in output if item.get("type") == "web_search_call"]
    raw = response.get("usage") or {}
    request = {"in": raw.get("input_tokens", 0), "out": raw.get("output_tokens", 0),
               "cache_read": (raw.get("input_tokens_details") or {}).get("cached_tokens", 0)}
    usage = {**request, "searches": len(searches), "requests": [request], "sources": [], "unmetered": not raw}
    if response.get("status") != "completed":
        reason = (response.get("incomplete_details") or {}).get("reason")
        message = "出力トークン上限に到達" if reason == "max_output_tokens" else "OpenAI の応答が未完了"
        raise AgentError(message, usage)
    if len(searches) > max_searches:
        raise AgentError("検索回数が設定上限を超過", usage)
    completed_searches = [s for s in searches if s.get("status") == "completed"]
    if not completed_searches:
        raise AgentError("公式ページの検索を完了できませんでした", usage)

    sources = []
    for item in completed_searches:
        action = item.get("action") or {}
        sources.extend(entry.get("url") for entry in action.get("sources") or [])
        sources.append(action.get("url"))
    parts = [part for item in output if item.get("type") == "message" for part in item.get("content", [])]
    if any(part.get("type") == "refusal" for part in parts):
        raise AgentError("OpenAI が回答を拒否", usage)
    texts = [part for part in parts if part.get("type") == "output_text"]
    for part in texts:
        sources.extend(a.get("url") for a in part.get("annotations", []) if a.get("type") == "url_citation")
    usage["sources"] = list(dict.fromkeys(url for url in sources if url_key(url)))
    try:
        data = json.loads("".join(part["text"] for part in texts))
    except (KeyError, ValueError):
        raise AgentError("OpenAI 応答の JSON を読めませんでした", usage) from None
    if not isinstance(data, dict):
        raise AgentError("OpenAI 応答の JSON がオブジェクトではありません", usage)
    return data, usage


def cost_usd(model, usage, pricing):
    """入力トークンのうちキャッシュヒット分にだけ割引単価を適用する。"""
    rates = pricing["models"].get(model)
    if not rates:
        return None
    total = usage["searches"] * pricing["web_search_per_1000"] / 1000
    for req in usage["requests"]:
        cached = min(req["cache_read"], req["in"])
        long = req["in"] > rates.get("long_threshold", float("inf"))
        input_rate = rates["in"] * (2 if long else 1)
        cached_rate = rates["cache_read"] * (2 if long else 1)
        output_rate = rates["out"] * (1.5 if long else 1)
        total += ((req["in"] - cached) * input_rate + cached * cached_rate + req["out"] * output_rate) / 1e6
    return round(total, 5)
