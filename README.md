# AIツール料金ウォッチ

主要AIツールの料金と無料枠をAIチームが毎日調査し、運営者が承認したものだけを公開するサイトです。料金の**変更履歴**を出典つきで記録していきます。

## AIチームの構成(API代を抑える設計)

| 役割 | 担当 | 何をするか | 呼ばれる頻度 |
|---|---|---|---|
| orchestrator | コード(0円) | 確認日が古い順に対象を選び、結果を振り分ける | 毎回 |
| researcher | **Haiku 5.5** × 6 並列 | 1ツールずつ公式料金ページを調べる。検索は公式ドメインに限定、2〜3回まで | 毎回 |
| checker | コード(0円) | 形式チェックと前回との差分判定。**変化がなければここで終わり** | 毎回 |
| reviewer | **Sonnet 5.5** | 「料金が変わった」「新規」「Haikuが自信なし」のときだけ独立に再調査 | 必要なときだけ(上限4件) |
| scout | Haiku 5.5 | 新しいツール候補を探す | 月曜だけ(またはチェックを入れたとき) |
| you | あなた | 12時に結果を見て承認 | 毎日 |

料金はほとんどの日は変わらないので、大半の日は安いHaikuだけで終わります。高いSonnetは「変化があったときの確認」にしか使いません。

Opusは使いません。この仕事は判断が単純で、Sonnetで十分だからです。

コストは毎回概算され、ダッシュボードとPRに表示されます。単価は `site.json` の `pricing` にあり、公式の料金ページで確認して更新してください。

## 毎日の操作

管理ダッシュボード `https://<ユーザー名>.github.io/ai-tool-lab/admin.html` をスマホのホーム画面に追加しておくと便利です。

| 時刻 | 操作 | 何が起きるか |
|---|---|---|
| 8:00 | **① 探索を開始** | AIチームが調査し、承認待ちのPRを作る(数分〜15分) |
| 12:00 | 結果を確認して **② 承認して公開** | サイトが再生成されて公開される |

- 「料金変更」と「新規」は、「GitHubで出典を確認 →」から出典を見てから承認してください。
- おかしければ **却下** を押します。2回押すと確定し、何も公開されません。

ボタンは次のように動きます。

- **GitHubトークンを設定した場合**：ダッシュボード下の「設定」に入れると、ボタンがその場で実行されます。
- **未設定の場合**：GitHubの該当画面(Run workflow / Merge / Close)が開きます。

トークンは fine-grained token を使い、権限は次のとおりにしてください。

- 対象：このリポジトリのみ
- 権限：Actions、Contents、Pull requests を Read and write

トークンはそのブラウザの中だけに保存されます。ダッシュボードは検索エンジンに載らない設定(noindex)ですが、URLを知っていれば誰でも見られます。表示されるのは調査状況だけで、秘密情報は含みません。

## 初期設定(最初の1回だけ・約15分)

1. GitHubで新しいリポジトリ(例：`ai-tool-lab`、Public)を作り、このフォルダの中身をすべてアップロードします。
2. `site.json` の `base_url` と `repo` を自分のものに書き換えます。
3. [Anthropic Console](https://console.anthropic.com/) で次の3つを行います。
   - APIキーの発行
   - 利用上限(Spend limit)の設定
   - Web検索の有効化
4. リポジトリの Settings で次の3か所を設定します。
   - **Secrets and variables → Actions**:`ANTHROPIC_API_KEY` を登録
   - **Actions → General → Workflow permissions**:「Read and write」を選び、「Allow GitHub Actions to create and approve pull requests」にチェック
   - **Pages → Source**:「GitHub Actions」を選択
5. ダッシュボードから ① を押します。初回は12ツールすべてが未確認なので、2日で埋まります。

## 収益化

- ASPに登録し、提携できたツールは `data/tools.json` の `affiliate_url` にリンクを入れます。
- そのツールのリンクには「PR」表示と `rel="sponsored"` が自動で付きます。
- 広告表記と運営方針ページは最初から入っています。
- Search Console に `sitemap.xml` を送信してください。

## 安全装置

次の結果はサイトに反映されず、PRの「反映しなかったもの」に表示されます。

- 出典がない
- 確信度が低い
- 料金が取れなかった
- Sonnetが却下した

レビューは1回あたり4件までです。超えた分は次回に回るので、API代が暴走しません。

`python3 tests/test_pipeline.py` を実行すると、APIを使わずにAIチームの流れ全体をテストできます。

## ファイル構成

```
site.json                      サイト・モデル・単価の設定
data/tools.json                ツールと料金
data/changelog.json            料金変更の履歴
data/run_latest.json           直近の探索ログ(ダッシュボード用)
scripts/explore.py             AIチーム(orchestrator / researcher / checker / reviewer / scout)
scripts/agents.py              Claude API クライアントとコスト計算
scripts/build.py               静的サイト + ダッシュボード生成
scripts/admin.html             ダッシュボードのテンプレート
.github/workflows/explore.yml  ① 探索
.github/workflows/deploy.yml   ② 公開
```
