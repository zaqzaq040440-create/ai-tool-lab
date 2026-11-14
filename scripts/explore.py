"""朝の「探索」: マルチエージェントで料金・無料枠を調査し、data/ を更新する。

  orchestrator (コード・無料)  対象ツールを選ぶ / 結果を振り分ける
  researcher   (Haiku × N)    1ツールずつ並列に公式料金ページを調べる。検索は公式ドメインに限定
  checker      (コード・無料)  形式チェックと前回との差分判定。変化がなければここで終わり
  reviewer     (Sonnet)       「変更あり」「新規」「自信なし」のときだけ独立に再調査して判定
  scout        (Haiku)        週1回など指定日だけ、新しいツール候補を探す
  human        (あなた)        12時にPRを見て承認(Merge)

環境変数 ANTHROPIC_API_KEY が必要。SCOUT=1 でスカウトを強制実行。
"""
import datetime as dt
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / ".explore"
JST = dt.timezone(dt.timedelta(hours=9))
CATEGORIES = "チャットAI|AI検索|翻訳|文書・メモ|画像・デザイン|コーディング|音声|動画|その他"

PLAN_SCHEMA = """{
  "plans": [{"name": "プラン名", "price": 20, "currency": "USD|JPY", "period": "月|年", "note": ""}],
  "free_tier": "無料プランの内容を1文で(なければ空文字)",
  "pricing_url": "料金ページのURL",
  "confidence": "high|medium|low",
  "sources": ["https://..."]
}"""

RULES = """ルール:
- 出典は公式サイトの料金ページを最優先。確認できない項目を推測で埋めない(その場合 confidence は low)。
- 個人向けプランのみ。「要問い合わせ」の法人プランは省略。年払い割引などは note に書く。
- 回答は ```json コードブロック1つだけ。説明文は不要。"""


def load(path, default):
    return json.loads(path.read_text("utf-8")) if path.exists() else default


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", "utf-8")


# ---------- 実行ログ(ダッシュボードが読む) ----------

class RunLog:
    def __init__(self, site):
        self.site = site
        self.started = time.time()
        self.agents, self.lines = [], []
        self.lock = threading.Lock()

    def say(self, who, msg):
        with self.lock:
            self.lines.append({"t": dt.datetime.now(JST).strftime("%H:%M:%S"), "who": who, "msg": msg})

    def agent(self, role, model, target, status, usage, sec, note=""):
        cost = agents.cost_usd(model, usage, self.site["pricing"]) if usage else 0
        with self.lock:
            self.agents.append({"role": role, "model": model, "target": target, "status": status,
                                "in": usage["in"] if usage else 0, "out": usage["out"] if usage else 0,
                                "searches": usage["searches"] if usage else 0,
                                "cost_usd": cost or 0, "sec": round(sec, 1), "note": note,
                                "_usage": usage})


# ---------- 検証(コード) ----------

def _url(u):
    return isinstance(u, str) and u.startswith(("https://", "http://"))


def clean_plans(plans):
    out = []
    for p in plans or []:
        try:
            price = float(p["price"])
        except (KeyError, TypeError, ValueError):
            continue
        if p.get("currency") not in ("JPY", "USD") or p.get("period") not in ("月", "年") or price < 0:
            continue
        out.append({"name": str(p.get("name", "")).strip()[:40],
                    "price": int(price) if price == int(price) else round(price, 2),
                    "currency": p["currency"], "period": p["period"],
                    "note": str(p.get("note", "")).strip()[:120]})
    return out


def normalize(d):
    """エージェントの出力を検証済みの形にする。使えなければ None。"""
    if not isinstance(d, dict):
        return None
    sources = [s for s in d.get("sources", []) if _url(s)][:5]
    plans = clean_plans(d.get("plans"))
    if not sources or not plans or d.get("confidence") not in ("high", "medium"):
        return None
    return {"plans": plans, "free_tier": str(d.get("free_tier", "")).strip()[:200], "sources": sources,
            "pricing_url": d.get("pricing_url") if _url(d.get("pricing_url")) else "",
            "confidence": d["confidence"]}


def plans_text(plans):
    if not plans:
        return "(なし)"
    return " / ".join(f"{p['name']} {'$' if p['currency'] == 'USD' else '¥'}{p['price']:,}/{p['period']}"
                      for p in plans)


def same_as_before(tool, data):
    return plans_text(tool.get("plans", [])) == plans_text(data["plans"]) and \
        tool.get("free_tier", "") == data["free_tier"]


# ---------- 各エージェント ----------

def pick_targets(tools, n):
    return sorted(tools, key=lambda t: t.get("last_verified") or "0000")[:n]


def researcher(ctx, tool):
    m = ctx["site"]["models"]["researcher"]
    known = f"前回の料金ページ: {tool['pricing_url']}\n" if tool.get("pricing_url") else ""
    prompt = (f"{tool['name']}({tool['vendor']}、公式 {tool['url']})の現在の料金プランと無料枠を調べてください。\n"
              f"{known}{RULES}\n\n出力形式:\n```json\n{PLAN_SCHEMA}\n```")
    tools = [agents.web_search_tool(2 if tool.get("pricing_url") else 3, tool.get("domains") or None)]
    t0 = time.time()
    try:
        text, usage = ctx["call"](ctx["key"], m, "あなたは料金調査の担当者です。事実だけを答えます。", prompt, tools, 2000)
        data = agents.parse_json(text)
    except agents.AgentError as e:
        ctx["log"].agent("researcher", m, tool["name"], "failed", None, time.time() - t0, str(e)[:120])
        ctx["log"].say("researcher", f"{tool['name']}: 失敗 {str(e)[:60]}")
        return None
    ctx["log"].agent("researcher", m, tool["name"], "done", usage, time.time() - t0)
    return data


def reviewer(ctx, tool, proposal, reason):
    m = ctx["site"]["models"]["reviewer"]
    before = plans_text(tool.get("plans", [])) if tool.get("last_verified") else "(未登録)"
    prompt = (f"調査担当(軽量モデル)の結果を、あなたが独立にWeb検索で確かめてください。\n"
              f"ツール: {tool['name']}({tool.get('vendor', '')}、公式 {tool['url']})\n"
              f"確認が必要な理由: {reason}\n前回掲載していた料金: {before}\n"
              f"調査担当の提案:\n{json.dumps(proposal, ensure_ascii=False)}\n\n"
              f"verdict は confirm(提案どおり正しい)/ correct(誤りがあり修正した)/ reject(確認できない)。\n"
              f"{RULES}\n\n出力形式:\n```json\n" + PLAN_SCHEMA[:-2] + ',\n  "verdict": "confirm|correct|reject",\n'
              '  "reason": "判定理由を1文で"\n}\n```')
    tools = [agents.web_search_tool(3, tool.get("domains") or None)]
    t0 = time.time()
    try:
        text, usage = ctx["call"](ctx["key"], m, "あなたは料金情報のファクトチェック担当です。慎重に判定します。",
                                  prompt, tools, 3000)
        data = agents.parse_json(text)
    except agents.AgentError as e:
        ctx["log"].agent("reviewer", m, tool["name"], "failed", None, time.time() - t0, str(e)[:120])
        return None, "レビュー失敗"
    verdict = data.get("verdict")
    ctx["log"].agent("reviewer", m, tool["name"], verdict or "?", usage, time.time() - t0,
                     str(data.get("reason", ""))[:120])
    ctx["log"].say("reviewer", f"{tool['name']}: {verdict} — {str(data.get('reason', ''))[:50]}")
    if verdict in ("confirm", "correct"):
        return normalize(data), str(data.get("reason", ""))
    return None, f"Sonnetが却下: {data.get('reason', '')}"


def scout(ctx, tools):
    m = ctx["site"]["models"]["scout"]
    n = ctx["site"]["max_new_candidates"]
    prompt = (f"日本の個人ユーザーに人気が出ている、次のリストにないAIツールを最大{n}件探し、料金も調べてください。\n"
              f"既存: {', '.join(t['name'] for t in tools)}\n{RULES}\n\n出力形式:\n```json\n"
              '{"new_tools": [{"id": "英小文字とハイフン", "name": "", "vendor": "", "category": "'
              + CATEGORIES + '", "url": "公式URL", "domains": ["公式ドメイン"], "why": "理由を1文で", '
              + PLAN_SCHEMA[1:-2] + "}]}\n```")
    t0 = time.time()
    try:
        text, usage = ctx["call"](ctx["key"], m, "あなたはAIツールの新製品リサーチ担当です。",
                                  prompt, [agents.web_search_tool(4)], 3000)
        data = agents.parse_json(text)
    except agents.AgentError as e:
        ctx["log"].agent("scout", m, "新ツール探索", "failed", None, time.time() - t0, str(e)[:120])
        return []
    cands = data.get("new_tools", [])[:n]
    ctx["log"].agent("scout", m, "新ツール探索", "done", usage, time.time() - t0, f"候補 {len(cands)} 件")
    ctx["log"].say("scout", f"候補 {len(cands)} 件: " + ", ".join(str(c.get("name")) for c in cands))
    return cands


# ---------- 司令塔(コード) ----------

def apply(tool, data, today, changelog):
    kind = "first" if not tool.get("last_verified") else ("same" if same_as_before(tool, data) else "changed")
    if kind == "changed":
        changelog.insert(0, {"date": today, "tool_id": tool["id"], "tool_name": tool["name"],
                             "before": plans_text(tool.get("plans", [])), "after": plans_text(data["plans"]),
                             "free_before": tool.get("free_tier", ""), "free_after": data["free_tier"],
                             "sources": data["sources"][:3]})
    before = plans_text(tool.get("plans", []))
    tool.update(plans=data["plans"], free_tier=data["free_tier"], sources=data["sources"],
                confidence=data["confidence"], last_verified=today)
    if data.get("pricing_url"):
        tool["pricing_url"] = data["pricing_url"]
    return kind, before


def run(site, tools, changelog, today, call_fn, api_key, do_scout=False):
    log = RunLog(site)
    ctx = {"site": site, "call": call_fn, "key": api_key, "log": log}
    report = {k: [] for k in ("changed", "new", "first", "same", "skipped")}

    targets = pick_targets(tools, site["tools_per_day"])
    log.say("orchestrator", f"対象 {len(targets)} 件: " + ", ".join(t["name"] for t in targets))

    # 1) Haiku 調査員を並列に起動
    log.say("orchestrator", f"researcher × {len(targets)} を並列起動")
    with ThreadPoolExecutor(max_workers=min(6, len(targets) or 1)) as ex:
        raw = dict(zip([t["id"] for t in targets], ex.map(lambda t: researcher(ctx, t), targets)))

    # 2) コードで検証・振り分け
    accept, review = [], []
    for t in targets:
        r = raw[t["id"]]
        if r is None:
            report["skipped"].append((t, "調査失敗"))
            continue
        data = normalize(r)
        if data is None:
            review.append((t, r, "調査担当の確信度が低い/出典なし"))
        elif not t.get("last_verified") or same_as_before(t, data):
            accept.append((t, data))
        else:
            review.append((t, r, f"料金が変わった可能性: {plans_text(t['plans'])} → {plans_text(data['plans'])}"))
    log.say("checker", f"変化なし/初回 {len(accept)} 件・要レビュー {len(review)} 件・失敗 "
                       f"{len(report['skipped'])} 件")

    # 3) スカウト(指定日だけ)
    new_entries = []
    if do_scout:
        by_id = {t["id"] for t in tools}
        for c in scout(ctx, tools):
            nid = re.sub(r"[^a-z0-9-]", "", str(c.get("id", "")).lower())
            if not nid or nid in by_id or not _url(c.get("url")):
                continue
            stub = {"id": nid, "name": str(c.get("name", nid))[:60], "vendor": str(c.get("vendor", ""))[:60],
                    "category": c.get("category") if c.get("category") in CATEGORIES.split("|") else "その他",
                    "url": c["url"], "domains": [d for d in c.get("domains", []) if isinstance(d, str)][:3],
                    "affiliate_url": "", "plans": [], "free_tier": "", "last_verified": "", "sources": []}
            new_entries.append(stub)
            review.append((stub, c, f"新規候補: {str(c.get('why', ''))[:80]}"))

    # 4) Sonnet レビュー(上限つき)
    limit = site["max_reviews_per_run"]
    if review:
        log.say("orchestrator", f"reviewer(Sonnet)へ {min(len(review), limit)} 件エスカレーション")
    for i, (t, proposal, reason) in enumerate(review):
        if i >= limit:
            report["skipped"].append((t, "レビュー上限に達したため次回へ"))
            continue
        data, why = reviewer(ctx, t, proposal, reason)
        if data is None:
            report["skipped"].append((t, why))
            continue
        if any(t is s for s in new_entries):
            tools.append(t)
            apply(t, data, today, changelog)
            report["new"].append((t, why))
        else:
            accept.append((t, data))

    # 5) 反映
    for t, data in accept:
        kind, before = apply(t, data, today, changelog)
        report[kind].append((t, f"{before} → {plans_text(t['plans'])}" if kind == "changed" else ""))

    # 集計
    tot = {"in": 0, "out": 0, "searches": 0, "cost_usd": 0.0}
    base_usage = {"in": 0, "out": 0, "cache_read": 0, "searches": 0, "requests": []}
    for a in log.agents:
        u = a.pop("_usage")
        for k in ("in", "out", "searches"):
            tot[k] += a[k]
        tot["cost_usd"] += a["cost_usd"]
        if u:
            base_usage["requests"] += u["requests"]
            base_usage["searches"] += u["searches"]
    baseline = agents.cost_usd(site["models"]["reviewer"], base_usage, site["pricing"]) or 0
    tot["cost_usd"] = round(tot["cost_usd"], 4)
    tot["cost_jpy"] = round(tot["cost_usd"] * site["pricing"]["usd_jpy"], 1)
    log.say("orchestrator", f"完了: 変更 {len(report['changed'])} / 新規 {len(report['new'])} / "
                            f"概算 ¥{tot['cost_jpy']}")

    run_info = {
        "date": today, "started_at": dt.datetime.fromtimestamp(log.started, JST).isoformat(timespec="seconds"),
        "duration_s": round(time.time() - log.started, 1), "scout": do_scout,
        "agents": log.agents, "log": log.lines, "totals": tot,
        "baseline_all_sonnet_usd": round(baseline, 4),
        "counts": {k: len(v) for k, v in report.items()},
        "items": {k: [{"name": t["name"], "detail": d} for t, d in v] for k, v in report.items()},
    }
    return report, run_info


def pr_body(report, run_info):
    def block(title, rows, detail=True):
        if not rows:
            return ""
        out = [f"### {title}({len(rows)})"]
        for t, d in rows:
            src = " ".join(f"[出典{i + 1}]({s})" for i, s in enumerate(t.get("sources", [])[:3]))
            out.append(f"- **{t['name']}**: {plans_text(t.get('plans', []))}"
                       f"{' — ' + d if (d and detail) else ''} {src}")
        return "\n".join(out) + "\n"

    tot = run_info["totals"]
    roles = {}
    for a in run_info["agents"]:
        k = f"{a['role']} ({a['model']})"
        roles[k] = roles.get(k, 0) + 1
    team = " / ".join(f"{k} ×{v}" for k, v in roles.items())
    return (f"## {run_info['date']} の探索結果\n\n"
            "承認前に、**変更あり**と**新規**の出典リンクだけ開いて確認してください。"
            "問題なければ Merge で公開。おかしければ Close で何も公開されません。\n\n"
            + block("⚠️ 料金・無料枠に変更あり(Sonnet確認済み)", report["changed"])
            + block("🆕 新規ツール(Sonnet確認済み)", report["new"])
            + block("初回登録", report["first"], False)
            + block("変化なし(確認日だけ更新)", report["same"], False)
            + block("反映しなかったもの", report["skipped"])
            + f"\n---\n**エージェント**: {team}  \n"
              f"**概算コスト**: ${tot['cost_usd']}(約¥{tot['cost_jpy']})・検索 {tot['searches']} 回・"
              f"全部Sonnetなら約${run_info['baseline_all_sonnet_usd']}\n")


def main():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY が設定されていません")
    site = load(ROOT / "site.json", {})
    tools = load(DATA / "tools.json", [])
    changelog = load(DATA / "changelog.json", [])
    now = dt.datetime.now(JST)
    do_scout = os.environ.get("SCOUT") == "1" or now.weekday() in site.get("scout_weekdays", [])
    report, info = run(site, tools, changelog, now.date().isoformat(), agents.call, key, do_scout)
    save(DATA / "tools.json", tools)
    save(DATA / "changelog.json", changelog)
    save(DATA / "run_latest.json", info)
    OUT.mkdir(exist_ok=True)
    (OUT / "pr_body.md").write_text(pr_body(report, info), "utf-8")
    print(json.dumps(info["counts"], ensure_ascii=False), info["totals"])


if __name__ == "__main__":
    main()
