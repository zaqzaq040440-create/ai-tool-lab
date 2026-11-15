# AIツール料金ウォッチ

主要AIツールの個人向け料金と無料枠を調べ、運営者が承認したデータをGitHub Pagesに掲載します。料金・無料枠の変更履歴には、確認日、変更前後の値、公式出典が残ります。

## スマホでの初期設定

GitHubはスマホのブラウザで開きます。`Settings` や `Run workflow` が見えない場合は、Safariのページメニューで「デスクトップ用Webサイトを表示」を選びます。

1. OpenAI Platformでこのサイト用のプロジェクトを選び、APIの請求設定と `gpt-6-luna`・`gpt-6.1-sol` の利用可否を確認します。`Limits` の月次支出上限を設定し、`Enforce a hard limit` をオンにします。初期値の目安は月 **$15**（1ドル150円の仮定で約2,250円）です。[OpenAI Platform](https://platform.openai.com/) · [支出上限の設定](https://developers.openai.com/api/docs/guides/spend-limits)
2. 保有するプロジェクトキーを使うか、新しいAPIキーを発行します。GitHubの `New repository secret` を開き、Nameに `OPENAI_API_KEY`、Secretにキーを入力して保存します。キーの入力先はこの欄です。[OpenAI API Keys](https://platform.openai.com/api-keys) · [GitHub Secrets](https://github.com/zaqzaq040440-create/ai-tool-lab/settings/secrets/actions/new)
3. GitHubの **Actions → General → Workflow permissions** で **Allow GitHub Actions to create and approve pull requests** をオンにして保存します。探索ワークフローに必要な書き込み権限は設定済みです。PRの承認・マージは運営者が行います。[Actions設定](https://github.com/zaqzaq040440-create/ai-tool-lab/settings/actions)
4. GitHubの **Pages → Build and deployment → Source** を **GitHub Actions** にします。公開ワークフローがまだ成功していなければ `main` を選び **Run workflow** を押します。成功後は公開URLと `/admin.html` の両方を開けます。[Pages設定](https://github.com/zaqzaq040440-create/ai-tool-lab/settings/pages) · [② 公開](https://github.com/zaqzaq040440-create/ai-tool-lab/actions/workflows/deploy.yml) · [公開サイト](https://zaqzaq040440-create.github.io/ai-tool-lab/) · [管理画面](https://zaqzaq040440-create.github.io/ai-tool-lab/admin.html)

## 最初の実API確認

1. GitHubの **① 探索 → Run workflow** でブランチを `main` にし、「初回の疎通確認」をオンにして実行します。ChatGPTの料金1件をLunaとSolが公式情報から調べます。スカウトはこのモードでは動きません。[① 探索](https://github.com/zaqzaq040440-create/ai-tool-lab/actions/workflows/explore.yml)
2. 実行結果の **Summary** で結果、出典、2モデルの利用、検索回数と概算を見ます。掲載候補がある場合、同じ画面に**承認待ちPR**のリンクが出ます。掲載内容に問題がなければ、そのPRで **Squash and merge** を選びます。見送るときは **Close pull request** を選びます。
3. **② 公開** が成功したら、公開サイトと変更履歴を開きます。初回掲載の料金と出典はツール個別ページに出ます。次回から疎通確認をオフにして通常探索を行います。[② 公開](https://github.com/zaqzaq040440-create/ai-tool-lab/actions/workflows/deploy.yml) · [公開サイト](https://zaqzaq040440-create.github.io/ai-tool-lab/)

## 毎日の操作（日本時間）

| 時刻 | 管理画面の操作 | 結果 |
|---|---|---|
| 8:00 | **① 探索を開始** | 確認日が古い順に最大6件。月曜はスカウトも実行し、ほかの日はチェックを入れて追加できます。 |
| 12:00 | 変更・新規・初回掲載の出典を確認し、**② 承認して公開** | 価格データのPRを人間がマージすると、GitHub Pagesに反映されます。却下する場合は **却下** を2回押します。 |

管理画面のボタンは、GitHubトークン未設定時にはGitHubの該当画面を開きます。直接操作したい場合は、このリポジトリ専用の **fine-grained token** を用意し、**Actions・Contents・Pull requests** をそれぞれ **Read and write** にして、管理画面下部の設定に入力します。トークンは開いているタブのセッションに保存されます。タブを閉じたら、直接操作を使う場合に再登録します。OpenAIのキーはGitHub Secretsで管理します。[Fine-grained tokenの作成](https://github.com/settings/personal-access-tokens/new)

管理画面と承認待ちPRは公開URLです。PRには価格、出典、検索回数、概算費用が出ます。探索で有効な変更がなければPRは作成せず、結果はActionsのSummaryから確認できます。新しい探索PRができた時点で前の探索PRを閉じます。

## 調査と費用

- 調査員・スカウトは **GPT-6 Luna**、再確認は **GPT-6.1 Sol**。調査員は公式ドメインに限定し、料金ページが既知なら検索上限は2回、未登録なら3回です。Solの確認は1件3回、1回の探索で最大4件です。スカウトは候補探しに4回まで検索し、掲載前にSolが公式ドメインで再確認します。
- 検索で返った公式URLにひもづく結果を採用候補にします。確信度が低い、価格が不明、出典を照合できない、再確認で見送った結果は掲載候補から外します。初期12件の掲載はSolの再確認が通ったものから順次行います。
- API応答のトークン数・検索回数と `site.json` の単価から概算します。OpenAIのWeb検索は **$0.01/回** に加え、モデルのトークン料金がかかります。1回の通常探索で検索料の上限は、スカウト込みで34回・$0.34です。実際の請求額はOpenAI Usageで確認します。[API料金](https://developers.openai.com/api/docs/pricing) · [OpenAI Usage](https://platform.openai.com/usage)

## 収益化と掲載方針

提携できたサービスの `affiliate_url` にリンクを登録します。該当するリンクには「PR」と `rel="sponsored"` が付き、各比較ページには広告の説明を掲載します。表示順は月額にそろえた最安の有料プランの料金で決めます。体験談を掲載する場合は実際に確認できたものに限ります。

## 開発

Pythonの標準ライブラリで動作します。`python3 tests/test_pipeline.py` はAPIなしで探索、再確認、データ更新、サイト生成を検証します。`python3 scripts/build.py` で `public/` を生成します。`/admin.html?demo` ではサンプルで操作を確認できます。実際の探索には `OPENAI_API_KEY` が必要です。
