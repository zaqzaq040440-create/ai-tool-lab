"""data/*.json から静的サイトを public/ に生成する(標準ライブラリのみ)。"""
import datetime as dt
import html
import json
import shutil
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUB = ROOT / "public"
JST = dt.timezone(dt.timedelta(hours=9))
e = html.escape

CSS = """
:root{--bg:#fafaf7;--fg:#1d1d1b;--mut:#6b6b66;--line:#e4e2dc;--acc:#2f5d50;--warn:#a4471c}
@media(prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--mut:#9b9a93;--line:#2c2b29;--acc:#7fb8a6;--warn:#e08a5f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.7 system-ui,-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:0 16px}
header{border-bottom:1px solid var(--line);padding:14px 0}header a{color:var(--fg);text-decoration:none;font-weight:700}
nav a{margin-left:14px;font-size:14px;color:var(--mut);font-weight:400}
.ad{font-size:12px;color:var(--mut);background:var(--line);padding:4px 16px}
h1{font-size:24px;margin:28px 0 4px}h2{font-size:19px;margin:32px 0 8px;border-left:4px solid var(--acc);padding-left:8px}
.lead{color:var(--mut);margin:0 0 8px}
.tbl{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{border-bottom:1px solid var(--line);padding:8px 6px;text-align:left;vertical-align:top}th{color:var(--mut);font-weight:600;white-space:nowrap}
td.n{white-space:nowrap}a{color:var(--acc)}
.pr{display:inline-block;font-size:11px;border:1px solid var(--mut);color:var(--mut);border-radius:3px;padding:0 4px;margin-left:4px}
.chg{color:var(--warn);font-weight:600}
.src{font-size:12px;color:var(--mut)}
footer{margin:48px 0 24px;font-size:12px;color:var(--mut);border-top:1px solid var(--line);padding-top:12px}
"""


def money(p):
    sym = "$" if p["currency"] == "USD" else "¥"
    return f"{sym}{p['price']:,}/{p['period']}"


def monthly_jpy(p, usd_jpy):
    amount = p["price"] * (usd_jpy if p["currency"] == "USD" else 1)
    return amount / 12 if p["period"] == "年" else amount


def cheapest(t, usd_jpy):
    paid = [p for p in t.get("plans", []) if p["price"] > 0]
    return min(paid, key=lambda p: monthly_jpy(p, usd_jpy), default=None)


def history_value(entry, key):
    price = e(entry[key])
    if entry.get("free_before", "") != entry.get("free_after", ""):
        return price + "<br>無料枠 " + e(entry.get(f"free_{key}") or "なし")
    return price


def history_sources(entry):
    return " ".join(f'<a href="{e(url)}" rel="noopener">出典{i}</a>'
                    for i, url in enumerate(entry.get("sources", []), 1))


def page(site, title, body, rel=""):
    full = f"{title} | {site['title']}" if title != site["title"] else title
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(full)}</title><meta name="description" content="{e(site['description'])}">
<style>{CSS}</style></head><body>
<div class="ad">当サイトはアフィリエイト広告を利用しています。「PR」表示のリンクは広告です。</div>
<header><div class="wrap"><a href="{rel}index.html">{e(site['title'])}</a>
<nav><a href="{rel}changes.html">変更履歴</a><a href="{rel}about.html">運営方針</a></nav></div></header>
<main class="wrap">{body}</main>
<footer class="wrap">料金は各公式サイトの表示をAIが調査し、運営者が承認して掲載しています。最新・正確な情報は必ず公式サイトでご確認ください。<br>
最終更新 {dt.datetime.now(JST):%Y-%m-%d}</footer></body></html>"""


def tool_link(t, rel=""):
    if t.get("affiliate_url"):
        return f'<a href="{e(t["affiliate_url"])}" rel="sponsored noopener" target="_blank">公式サイト</a><span class="pr">PR</span>'
    return f'<a href="{e(t["url"])}" rel="noopener" target="_blank">公式サイト</a>'


def build(site, tools, changelog, run=None):
    if PUB.exists():
        shutil.rmtree(PUB)
    (PUB / "tools").mkdir(parents=True)
    live = [t for t in tools if t.get("last_verified") and t.get("plans")]
    recent = {c["tool_id"] for c in changelog[:30]}
    urls = ["index.html", "changes.html", "about.html"]
    rate = site.get("pricing", {}).get("usd_jpy", 150)

    # トップ:カテゴリ別比較表
    cats = defaultdict(list)
    for t in live:
        cats[t["category"]].append(t)
    parts = [f"<h1>{e(site['title'])}</h1><p class='lead'>{e(site['description'])}。"
             f"掲載 {len(live)} ツール。</p>"]
    if changelog:
        c = changelog[0]
        parts.append(f"<p>最新の変更: <span class='chg'>{e(c['date'])} {e(c['tool_name'])}</span> "
                     f"<a href='changes.html'>変更履歴を見る</a></p>")
    if live:
        parts.append(f"<p class='src'>最安の有料プランを月額にそろえ、1 USD＝{rate:g}円の固定レートで比較しています。</p>")
    for cat in sorted(cats):
        rows = []
        entries = [(t, cheapest(t, rate)) for t in cats[cat]]
        for t, cp in sorted(entries, key=lambda pair: monthly_jpy(pair[1], rate) if pair[1] else float("inf")):
            mark = " <span class='chg'>●最近変更</span>" if t["id"] in recent else ""
            rows.append(
                f"<tr><td><a href='tools/{e(t['id'])}.html'>{e(t['name'])}</a>{mark}</td>"
                f"<td class='n'>{e(money(cp)) if cp else '—'}</td><td>{e(t.get('free_tier') or '—')}</td>"
                f"<td class='n'>{e(t['last_verified'])}</td><td class='n'>{tool_link(t)}</td></tr>")
        parts.append(f"<h2>{e(cat)}</h2><div class='tbl'><table><tr><th>ツール</th><th>最安の有料プラン</th>"
                     f"<th>無料枠</th><th>確認日</th><th></th></tr>{''.join(rows)}</table></div>")
    if not live:
        parts.append("<p>確認済みの料金を準備中です。</p>")
    (PUB / "index.html").write_text(page(site, site["title"], "".join(parts)), "utf-8")

    # ツール個別ページ
    for t in live:
        hist = [c for c in changelog if c["tool_id"] == t["id"]]
        plan_rows = "".join(f"<tr><td>{e(p['name'])}</td><td class='n'>{e(money(p))}</td><td>{e(p.get('note', ''))}</td></tr>"
                            for p in t["plans"])
        hist_html = "".join(f"<li>{e(c['date'])}<br>{history_value(c, 'before')} → <b>{history_value(c, 'after')}</b> "
                            f"<span class='src'>{history_sources(c)}</span></li>" for c in hist) \
            or "<li>記録開始以降、変更はありません。</li>"
        srcs = "".join(f"<li><a href='{e(s)}' rel='noopener' target='_blank'>{e(s)}</a></li>" for s in t.get("sources", []))
        body = (f"<h1>{e(t['name'])} の料金と無料枠</h1><p class='lead'>{e(t['vendor'])} / {e(t['category'])} / "
                f"確認日 {e(t['last_verified'])}</p><p>{tool_link(t, '../')}</p>"
                f"<h2>料金プラン</h2><div class='tbl'><table><tr><th>プラン</th><th>料金</th><th>備考</th></tr>{plan_rows}</table></div>"
                f"<h2>無料枠</h2><p>{e(t.get('free_tier') or '—')}</p>"
                f"<h2>料金の変更履歴</h2><ul>{hist_html}</ul>"
                f"<h2>出典</h2><ul class='src'>{srcs}</ul>")
        (PUB / "tools" / f"{t['id']}.html").write_text(page(site, f"{t['name']}の料金", body, "../"), "utf-8")
        urls.append(f"tools/{t['id']}.html")

    # 変更履歴
    items = "".join(
        f"<tr><td class='n'>{e(c['date'])}</td><td><a href='tools/{e(c['tool_id'])}.html'>{e(c['tool_name'])}</a></td>"
        f"<td>{history_value(c, 'before')}</td><td><b>{history_value(c, 'after')}</b></td>"
        f"<td class='src'>{history_sources(c)}</td></tr>"
        for c in changelog)
    body = ("<h1>料金・無料枠の変更履歴</h1><p class='lead'>毎日の調査で見つかった変更を記録しています。</p>"
            + (f"<div class='tbl'><table><tr><th>日付</th><th>ツール</th><th>変更前</th><th>変更後</th><th></th></tr>{items}</table></div>"
               if changelog else "<p>まだ変更は記録されていません。</p>"))
    (PUB / "changes.html").write_text(page(site, "変更履歴", body), "utf-8")

    about = ("<h1>運営方針</h1>"
             "<p>このサイトは、AIが毎日公式サイトなどを調査して料金・無料枠を更新し、運営者が内容を確認・承認してから公開しています。</p>"
             "<p>掲載情報には出典URLを付けています。AIの調査には誤りが含まれる可能性があるため、契約前には必ず公式サイトをご確認ください。</p>"
             "<p>当サイトはアフィリエイト広告を利用しており、「PR」と表示したリンク経由でのお申し込みにより運営者が報酬を受け取ることがあります。"
             "報酬の有無は掲載順や評価に影響しません(比較表は料金の安い順に並べています)。</p>")
    (PUB / "about.html").write_text(page(site, "運営方針", about), "utf-8")

    # 管理ダッシュボード(検索に出さない)
    sample_p = ROOT / "scripts" / "sample_run.json"
    cfg = {"repo": site.get("repo", ""), "base_url": site.get("base_url", ""),
           "tools_per_day": site.get("tools_per_day", 6), "pricing": site.get("pricing", {}),
           "run": run, "sample": json.loads(sample_p.read_text("utf-8")) if sample_p.exists() else None}
    tpl = (ROOT / "scripts" / "admin.html").read_text("utf-8")
    (PUB / "admin.html").write_text(
        tpl.replace("__CONFIG__", json.dumps(cfg, ensure_ascii=False).replace("<", "\\u003c")), "utf-8")

    base = site["base_url"].rstrip("/")
    sm = "".join(f"<url><loc>{e(base)}/{u}</loc></url>" for u in urls)
    (PUB / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sm}</urlset>', "utf-8")
    (PUB / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n", "utf-8")
    return len(live)


if __name__ == "__main__":
    s = json.loads((ROOT / "site.json").read_text("utf-8"))
    rp = ROOT / "data/run_latest.json"
    n = build(s, json.loads((ROOT / "data/tools.json").read_text("utf-8")),
              json.loads((ROOT / "data/changelog.json").read_text("utf-8")),
              json.loads(rp.read_text("utf-8")) if rp.exists() else None)
    print(f"built {n} tool pages into {PUB}")
