# AIC速報

実際のニュース(RSS)を題材に、Claude APIで要約とAIコメントを生成する
まとめサイトの自動生成パイプライン。

## セットアップ

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## 実行

```powershell
# APIキーを設定して本番生成(1日10本)
$env:ANTHROPIC_API_KEY = "sk-ant-..."
.venv\Scripts\python generate.py

# キー未設定の場合はモックモード(動作確認用のダミー記事)で動く
.venv\Scripts\python generate.py
```

生成結果は `site/` に出力される(`index.html` + 記事ページ)。

## 自動運用(GitHub Actions)

1. このフォルダをGitHubリポジトリとしてpush
2. リポジトリの Settings → Secrets and variables → Actions に
   `ANTHROPIC_API_KEY` を登録
3. Settings → Pages で公開ブランチ・`/site` ディレクトリを指定
4. `.github/workflows/daily.yml` が毎朝6時(JST)に10本生成して自動公開

## 人気記事分析(1日1本の特化記事)

GA4を導入後、以下を設定すると `analyze.py` が直近7日の人気記事を分析し、
翌日の1本目の編集方針(`focus_hint.txt`)を自動生成する:

- Secrets に `GA4_PROPERTY_ID`(例: `properties/123456789`)
- GA4サービスアカウントの認証JSON(`GOOGLE_APPLICATION_CREDENTIALS`)
- `requirements.txt` の `google-analytics-data` を有効化
- `templates/base.html` のGA4計測タグのコメントアウトを解除

## 法的注意(必読)

- ニュース本文の転載は著作権侵害。要約はAIによる独自文章のみ、出典リンク必須
- AIコメントである旨の表示(テンプレートに組込済み)は削除しないこと
  (ステルスマーケティング規制対策)
- AdSense申請時は「付加価値のない自動生成コンテンツ」と判定されないよう、
  独自の論点整理・キャラ設定を維持すること
