"""OpenAI API を使わずに探索・確認・サイト生成を検証する。"""
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import agents  # noqa: E402
import build  # noqa: E402
import explore  # noqa: E402

SITE = json.loads((ROOT / "site.json").read_text("utf-8"))
URL = "https://openai.com/chatgpt/pricing/"


def price_data(url, price=20, free="無料枠あり"):
    return {"plans": [{"name": "Plus", "price": price, "currency": "USD", "period": "月", "note": ""}],
            "free_tier": free, "pricing_url": url, "confidence": "high", "sources": [url]}


def usage(sources):
    req = {"in": 8000, "out": 600, "cache_read": 400}
    return {**req, "searches": 1, "requests": [req], "sources": sources}


def response():
    return {"status": "completed", "output": [
        {"type": "web_search_call", "status": "completed", "action": {"type": "search", "sources": [{"url": URL}]}},
        {"type": "message", "content": [{"type": "output_text", "text": json.dumps(price_data(URL)), "annotations": []}]},
    ], "usage": {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 200}, "output_tokens": 400}}


def http_error(code):
    return urllib.error.HTTPError(agents.API_URL, 429, "rate limit", {"Retry-After": "0"},
                                  io.BytesIO(json.dumps({"error": {"code": code}}).encode()))


class ResponsesTests(unittest.TestCase):
    def call(self):
        return agents.call("test-key", "gpt-6-luna", "公式のみ", "ChatGPTの価格", explore.RESEARCH_SCHEMA,
                           2, ["openai.com"], 2400)

    def test_response_format_search_controls_and_metering(self):
        with patch.object(agents.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(response()).encode())) as api:
            data, used = self.call()
        req = api.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(req.get_header("Authorization"), "Bearer test-key")
        sent = json.loads(req.data)
        self.assertEqual(sent["model"], "gpt-6-luna")
        self.assertEqual(sent["max_tool_calls"], 2)
        self.assertEqual(sent["max_output_tokens"], 2400)
        self.assertEqual(sent["tools"][0]["filters"]["allowed_domains"], ["openai.com"])
        self.assertEqual(sent["tool_choice"], {"type": "web_search"})
        self.assertTrue(sent["text"]["format"]["strict"])
        self.assertFalse(sent["store"])
        self.assertEqual(data, price_data(URL))
        self.assertEqual(used["sources"], [URL])
        self.assertEqual(used["cache_read"], 200)
        self.assertEqual(agents.cost_usd("gpt-6-luna", used, SITE["pricing"]), 0.01028)

    def test_partial_and_refused_responses_preserve_usage(self):
        incomplete = {**response(), "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}
        refused = response()
        refused["output"][1]["content"] = [{"type": "refusal", "refusal": "test"}]
        no_search = response()
        no_search["output"].pop(0)
        for result, expected, calls in [(incomplete, "上限", 1), (refused, "拒否", 1), (no_search, "検索", 0)]:
            with self.subTest(expected=expected), patch.object(agents, "_send", return_value=result):
                with self.assertRaisesRegex(agents.AgentError, expected) as error:
                    self.call()
                self.assertEqual(error.exception.usage["searches"], calls)
                self.assertEqual(error.exception.usage["out"], 400)

    def test_rate_limit_retry_and_budget_stop(self):
        with patch.object(agents.time, "sleep"), patch.object(agents.urllib.request, "urlopen", side_effect=[
            http_error("rate_limit_exceeded"), io.BytesIO(json.dumps(response()).encode()),
        ]) as api:
            self.call()
            self.assertEqual(api.call_count, 2)
        with patch.object(agents.urllib.request, "urlopen", side_effect=http_error("project_spend_limit_exceeded")) as api:
            with self.assertRaisesRegex(agents.AgentError, "支出上限"):
                self.call()
            self.assertEqual(api.call_count, 1)
        with patch.object(agents.urllib.request, "urlopen", side_effect=TimeoutError()) as api:
            with self.assertRaises(agents.AgentError) as error:
                self.call()
            self.assertTrue(error.exception.unmetered)
            self.assertEqual(api.call_count, 1)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tools = json.loads((ROOT / "data/tools.json").read_text("utf-8"))
        self.site = dict(SITE)
        self.history = []
        self.price, self.free = 20, "無料枠あり"
        self.reviewer_rejects = False
        self.calls = []

    def model_call(self, key, model, system, prompt, schema, max_searches, domains, max_tokens):
        self.calls.append((model, max_searches, domains))
        if schema == explore.SCOUT_SCHEMA:
            new = {"id": "new-tool", "name": "NewTool", "vendor": "V", "category": "動画",
                   "url": "https://new.example/", "why": "公式サイトで確認"}
            return {"new_tools": [new, new]}, usage(["https://new.example/pricing"])
        url = URL if "openai.com" in domains else f"https://{domains[0]}/pricing"
        data = price_data(url, self.price, self.free)
        if schema == explore.REVIEW_SCHEMA:
            data.update(verdict="reject" if self.reviewer_rejects else "confirm", reason="出典確認")
        return data, usage([url])

    def explore(self, date, **kwargs):
        return explore.run(self.site, self.tools, self.history, date, self.model_call, "k", **kwargs)

    def test_initial_setup_caps_review_and_scout(self):
        report, info = self.explore("2026-11-16", do_scout=True)
        self.assertEqual(sum(a["role"] == "researcher" for a in info["agents"]), 6)
        self.assertEqual(len(report["first"]), 3)
        self.assertEqual(len(report["new"]), 1)
        self.assertEqual(len(report["skipped"]), 3)
        self.assertEqual(sum(a["role"] == "reviewer" for a in info["agents"]), 4)
        self.assertLess(info["totals"]["cost_usd"], info["baseline_all_reviewer_usd"])
        self.assertEqual(self.history, [])
        self.assertIn("料金の初回掲載", explore.pr_body(report, info))
        self.assertTrue(all(domains for _, _, domains in self.calls if domains is not None))

    def test_exact_source_and_valid_amount_required(self):
        self.assertIsNone(explore.normalize(price_data(URL), ["https://openai.com/other"], ["openai.com"]))
        self.assertIsNone(explore.normalize(price_data("https://openai.com.bad.test/pricing"),
                                           ["https://openai.com.bad.test/pricing"], ["openai.com"]))
        self.assertIsNone(explore.normalize(price_data(URL, float("nan")), [URL], ["openai.com"]))
        self.assertIsNone(explore.normalize(price_data(URL, True), [URL], ["openai.com"]))
        self.assertIsNone(explore.normalize(dict(price_data(URL), confidence="low"), [URL], ["openai.com"]))
        self.assertEqual(explore.normalize(price_data(URL + "?utm_source=demo#plan"), [URL], ["openai.com"])["sources"], [URL])

    def test_smoke_unchanged_free_change_rejection_and_site_build(self):
        self.tools = [t for t in self.tools if t["id"] == "chatgpt"]
        report, info = self.explore("2026-11-15", smoke=True, do_scout=True)
        self.assertEqual([a["role"] for a in info["agents"]], ["researcher", "reviewer"])
        self.assertEqual(len(report["first"]), 1)
        self.assertEqual(self.tools[0]["pricing_url"], URL)
        self.assertIn(URL, explore.pr_body(report, info))
        self.calls.clear()
        report, _ = self.explore("2026-11-16")
        self.assertEqual(len(report["same"]), 1)
        self.assertEqual(self.calls, [("gpt-6-luna", 2, ["openai.com", "chatgpt.com"])])
        self.free = "無料枠は月5回"
        report, info = self.explore("2026-11-17")
        self.assertEqual(len(report["changed"]), 1)
        self.assertEqual(self.history[0]["free_before"], "無料枠あり")
        self.assertEqual(self.history[0]["free_after"], self.free)
        self.assertIn("無料枠 無料枠あり → 無料枠は月5回", explore.pr_body(report, info))
        self.price = 25
        self.reviewer_rejects = True
        report, _ = self.explore("2026-11-18")
        self.assertEqual(len(report["skipped"]), 1)
        self.assertEqual(self.tools[0]["plans"][0]["price"], 20)
        self.assertEqual(len(self.history), 1)
        self.reviewer_rejects = False
        report, _ = self.explore("2026-11-19")
        self.assertEqual(len(report["changed"]), 1)
        self.assertEqual(self.history[0]["after"], "Plus $25/月")

        self.tools[0]["affiliate_url"] = "https://aff.example/chatgpt"
        with tempfile.TemporaryDirectory() as out, patch.object(build, "PUB", Path(out) / "public"):
            self.assertEqual(build.build(self.site, self.tools, self.history, info), 1)
            index = (build.PUB / "index.html").read_text("utf-8")
            self.assertIn('rel="sponsored noopener"', index)
            self.assertIn("PR</span>", index)
            self.assertIn("最近変更", index)
            for page in ["changes.html", "tools/chatgpt.html"]:
                content = (build.PUB / page).read_text("utf-8")
                self.assertIn("月5回", content)
                self.assertIn(URL, content)
            self.assertTrue((build.PUB / "admin.html").exists())
            self.assertNotIn("admin.html", (build.PUB / "sitemap.xml").read_text("utf-8"))

    def test_failed_smoke_records_diagnostics_without_price_data(self):
        with tempfile.TemporaryDirectory() as out:
            root = Path(out)
            (root / "data").mkdir()
            explore.save(root / "site.json", self.site)
            explore.save(root / "data/tools.json", self.tools)
            self.reviewer_rejects = True
            with patch.object(explore, "ROOT", root), patch.object(explore, "DATA", root / "data"), \
                 patch.object(explore, "OUT", root / ".explore"), patch.object(agents, "call", self.model_call), \
                 patch.dict(explore.os.environ, {"OPENAI_API_KEY": "test-key", "SMOKE": "1"}):
                with self.assertRaisesRegex(SystemExit, "掲載候補を作れませんでした"):
                    explore.main()
            self.assertEqual(explore.load(root / "data/tools.json", []), self.tools)
            self.assertFalse((root / "data/run_latest.json").exists())
            self.assertIn("Solの判定で見送り", (root / ".explore/pr_body.md").read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
