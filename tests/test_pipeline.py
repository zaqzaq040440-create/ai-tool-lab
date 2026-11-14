"""API を呼ばずにマルチエージェントの流れとサイト生成をテストする。 python3 tests/test_pipeline.py"""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import agents  # noqa: E402
import build  # noqa: E402
import explore  # noqa: E402

site = json.loads((ROOT / "site.json").read_text("utf-8"))
site["tools_per_day"] = 12
tools = copy.deepcopy(json.loads((ROOT / "data/tools.json").read_text("utf-8")))
changelog = []
PRICE = {"chatgpt": 20}
calls = []


def plan(price, cur="USD", conf="high", src=True):
    return {"plans": [{"name": "Pro", "price": price, "currency": cur, "period": "月"}],
            "free_tier": "無料プランあり", "pricing_url": "https://example.com/pricing",
            "confidence": conf, "sources": ["https://example.com/pricing"] if src else []}


def fake_call(key, model, system, prompt, tools_, max_tokens):
    calls.append((model, system[:12], tools_[0].get("allowed_domains")))
    tin, tout = (26000, 900) if "sonnet" in model else (17000, 600)   # 検索結果込みの想定値
    n = tools_[0]["max_uses"] - (1 if "sonnet" not in model else 0)
    usage = {"in": tin, "out": tout, "cache_read": 0, "searches": n,
             "requests": [{"in": tin, "out": tout, "cache_read": 0}]}
    if "新製品" in system:
        out = {"new_tools": [dict(plan(10), id="new-tool", name="NewTool", vendor="V", category="動画",
                                  url="https://new.example", domains=["new.example"], why="話題")]}
    elif "ファクトチェック" in system:
        out = dict(plan(PRICE["chatgpt"] if "ChatGPT" in prompt else 18), verdict="confirm", reason="公式で確認")
        if "Gemini" in prompt:
            out["verdict"] = "reject"
    else:
        if "Gemini" in prompt:
            raise agents.AgentError("HTTP 529: overloaded")
        if "Claude(" in prompt:
            out = plan(18, conf="low")
        elif "Perplexity" in prompt:
            out = plan(20, src=False)
        elif "ChatGPT" in prompt:
            out = plan(PRICE["chatgpt"])
        else:
            out = plan(10, "JPY")
    return "調べました\n```json\n" + json.dumps(out, ensure_ascii=False) + "\n```", usage


# 1日目: 全件初回 + スカウト
r1, info1 = explore.run(site, tools, changelog, "2026-11-16", fake_call, "k", do_scout=True)
c = info1["counts"]
assert c["first"] == 11, c   # 12 - gemini失敗(claude/perplexity は Sonnet 確認後に登録)
assert c["skipped"] == 1 and c["new"] == 1, c    # gemini 失敗 / new-tool 追加
roles = [a["role"] for a in info1["agents"]]
assert roles.count("researcher") == 12 and roles.count("scout") == 1
assert roles.count("reviewer") == 3, roles       # claude, perplexity, new-tool
assert any(d == ["openai.com", "chatgpt.com"] for _, _, d in calls), "公式ドメイン限定が効いていない"
assert any(t["id"] == "new-tool" for t in tools)
assert info1["totals"]["cost_usd"] < info1["baseline_all_sonnet_usd"]

# 2日目: ChatGPT が値上げ → Sonnet 確認 → 変更履歴へ
PRICE["chatgpt"] = 25
calls.clear()
r2, info2 = explore.run(site, tools, changelog, "2026-11-17", fake_call, "k")
assert info2["counts"]["changed"] == 1, info2["counts"]
assert changelog[0]["before"] == "Pro $20/月" and changelog[0]["after"] == "Pro $25/月"
assert sum(1 for m, *_ in calls if "sonnet" in m) == 3   # chatgpt, claude(low), perplexity(no src)
body = explore.pr_body(r2, info2)
assert "変更あり" in body and "概算コスト" in body
print(body)
print("haiku:", sum(a["cost_usd"] for a in info2["agents"] if "haiku" in a["model"]),
      "total:", info2["totals"], "all-sonnet:", info2["baseline_all_sonnet_usd"])

# サイト生成
tools[0]["affiliate_url"] = "https://aff.example/chatgpt"
(ROOT / "data").mkdir(exist_ok=True)
json.dump(info2, open(ROOT / "scripts/sample_run.json", "w"), ensure_ascii=False, indent=2)
n = build.build(site, tools, changelog, info2)
idx = (build.PUB / "index.html").read_text("utf-8")
assert 'rel="sponsored noopener"' in idx and "PR</span>" in idx and "最近変更" in idx
assert (build.PUB / "admin.html").exists()
assert "admin.html" not in (build.PUB / "sitemap.xml").read_text("utf-8")
print(f"built {n} pages. ALL TESTS PASSED")
