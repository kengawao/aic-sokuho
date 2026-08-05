# -*- coding: utf-8 -*-
"""まとめサイト記事生成パイプライン。

RSSからニュースを取得し、Claude APIで要約とAIコメントを生成して
静的HTMLを docs/ に出力する(日本語+英語の2言語)。

使い方:
  python generate.py                # 通常実行: ニュース10本+面白ネタ1本
  python generate.py --column-only  # 面白ネタ1本のみ(不定期の午後投稿用。約4割の確率でスキップ)

ANTHROPIC_API_KEY が未設定の場合はモックモードで動作し、
API呼び出しの代わりにサンプルデータで記事を生成する(動作確認用)。
"""
import argparse
import datetime
import json
import os
import pathlib
import random
import re
import sqlite3
import sys

import feedparser
from jinja2 import Environment, FileSystemLoader

BASE = pathlib.Path(__file__).parent
DB = BASE / "matome.db"
SITE = BASE / "docs"
FOCUS_HINT_FILE = BASE / "focus_hint.txt"

DAILY_LIMIT = 10
COMMENTS_PER_ARTICLE = 30
MODEL = "claude-haiku-4-5-20251001"

# 話題性フィルタに渡す候補の上限(多いほど選択肢が増えるがプロンプトが長くなる)
CANDIDATE_POOL = 80

SITE_BASE_URL = "https://kengawao.github.io/aic-sokuho/"

RSS_FEEDS = [
    "https://news.yahoo.co.jp/rss/topics/top-picks.xml",
    "https://news.yahoo.co.jp/rss/topics/domestic.xml",
    "https://news.yahoo.co.jp/rss/topics/entertainment.xml",
    "https://news.yahoo.co.jp/rss/topics/it.xml",
    "https://news.yahoo.co.jp/rss/topics/business.xml",
    "https://news.yahoo.co.jp/rss/topics/sports.xml",
    "https://news.yahoo.co.jp/rss/topics/world.xml",
    "https://news.yahoo.co.jp/rss/topics/science.xml",
    "https://www.nhk.or.jp/rss/news/cat0.xml",
    # 国際ニュース強化用(取得失敗しても他フィードで継続する)
    "https://feeds.bbci.co.uk/japanese/rss.xml",
    "https://www.cnn.co.jp/rss/cnn/cnn.rdf",
    "https://www.afpbb.com/rss/afpbb/afpbbnews.rdf",
]

INTL_LIMIT = 5   # 国際記事の追加本数
NICHE_LIMIT = 5  # ニッチ記事の追加本数

MOCK_MODE = not os.environ.get("ANTHROPIC_API_KEY")

# 画面表示ラベル(日英)
T = {
    "ja": {
        "site_desc": "実際のニュースにコメンテーターたちが反応するまとめサイトです。",
        "meta_desc": "実際のニュースにコメンテーターたちが反応するまとめサイト。毎朝10本の注目ニュースを掲載。",
        "points": "ここがポイント",
        "comments": "コメント",
        "commenter": "つぶやき",
        "related": "関連記事",
        "back": "← 記事一覧へ戻る",
        "source": "出典",
        "footer_copy": "出典記事の著作権は各報道機関に帰属します。",
        "footer_ai": "コメントはすべてAI作成です",
        "switch": "English",
        "tab_all": "すべて",
        "ad": "広告スペース",
        "no_articles": "まだ記事がありません。",
    },
    "en": {
        "site_desc": "A roundup site where commentators react to real Japanese news.",
        "meta_desc": "A roundup site where commentators react to real Japanese news. 10 hot topics every morning.",
        "points": "Key Points",
        "comments": "Comments",
        "commenter": "Anon",
        "related": "Related Articles",
        "back": "← Back to all articles",
        "source": "Source",
        "footer_copy": "All source-article copyrights belong to the respective news organizations.",
        "footer_ai": "All comments are AI-generated",
        "switch": "日本語",
        "tab_all": "All",
        "ad": "Ad space",
        "no_articles": "No articles yet.",
    },
}

# カテゴリ(key=DB格納値、labelは言語別)
CATEGORIES = [
    {"key": "国内", "ja": "国内", "en": "Domestic"},
    {"key": "国際", "ja": "国際", "en": "World"},
    {"key": "経済", "ja": "経済", "en": "Business"},
    {"key": "エンタメ", "ja": "エンタメ", "en": "Entertainment"},
    {"key": "スポーツ", "ja": "スポーツ", "en": "Sports"},
    {"key": "IT", "ja": "IT", "en": "Tech"},
    {"key": "健康", "ja": "健康", "en": "Health"},
    {"key": "科学", "ja": "科学", "en": "Science"},
    {"key": "コラム", "ja": "コラム", "en": "Column"},
]

TONE_FUN = """今回は「面白ネタ・コラム」枠です。ニュースを堅く伝えるのではなく、
ゆるく面白おかしく紹介するコラム調で書くこと。コメントもボケとツッコミ多めで
楽しい雰囲気にすること。categoryは必ず「コラム」にすること。"""

PROMPT = """あなたはまとめサイトの編集者です。以下のニュースを題材に記事を作成してください。
{tone}
ニュース見出し: {title}
RSS概要: {summary}
出典URL: {url}
{focus_hint}

以下のJSON形式のみで出力してください:
{{
  "catchy_title": "クリックしたくなる記事タイトル。次の型を状況に応じて使う: 具体的な数字を入れる/「なぜ」「どうなる?」等の疑問形/意外性の対比(〜のはずが〜)/読者への問いかけ。ただし事実に反する誇張・釣りは禁止",
  "category": "国内/経済/エンタメ/スポーツ/IT/国際/健康/科学/コラム のいずれか",
  "summary": "ニュースの内容を自分の言葉で書いた200字程度の要約(原文のコピー禁止)",
  "points": ["論点1", "論点2", "論点3"],
  "comments": ["コメント本文", ...],
  "en": {{
    "catchy_title": "英語版タイトル(直訳でなく英語圏で自然なもの)",
    "summary": "英語版要約",
    "points": ["英語版の論点", ...],
    "comments": ["commentsと同じ件数・同じ順序で、自然な英語のネット掲示板風に訳したもの。>>番号アンカーは維持"]
  }}
}}
commentsは{n_comments}件。匿名掲示板(5ちゃんねる)風のスレッドとして書くこと:
- 口語・短文中心。「これは草」「マジかよ」のようなネットスラングも適度に使う
- 全体の3〜4割は「>>5 それは違うだろ」のように >>レス番号 で
  先行コメントへ返信し、会話・議論が続いているように見せる
  (返信先は必ず自分より小さい番号にすること)
- 賛成派・反対派・冷静な分析派・ツッコミ役・雑学披露役など視点を分散させる
- 全体の1〜2割は「エビデンス重視のオタク型」コメントにする: 研究・公的機関の
  ガイドライン・統計・歴史的経緯などの根拠を挙げて記事を批評・補足する
  (例: 「WHOのガイドラインだと〜のはず」「この手の話はコホート研究だと〜とされてる」
  「メーカーの決算資料見ると実は〜」のような、詳しい人が早口で語る雰囲気)
- オタク型コメントには、素人目線の賛同・反論・質問のレス(>>アンカー)を続けて
  議論に厚みを出す(「>>12 ソースあるの?」「>>12 これは正しい。補足すると〜」等)
- 重要: 実在しない論文名・著者名・雑誌名・DOI・細かい数値を捏造しないこと。
  確信が持てない知識は「〜という報告があったはず」「うろ覚えだが〜」程度の表現に留める
- 誹謗中傷・断定的なデマ・実在人物への攻撃・差別表現は禁止"""


def init_db():
    con = sqlite3.connect(DB)
    con.execute(
        """CREATE TABLE IF NOT EXISTS articles(
            id INTEGER PRIMARY KEY,
            url TEXT UNIQUE,
            title TEXT,
            category TEXT,
            created TEXT,
            slug TEXT)"""
    )
    # 既存DBへの列追加(初回のみ実行される)
    for ddl in ("ALTER TABLE articles ADD COLUMN has_en INTEGER DEFAULT 0",
                "ALTER TABLE articles ADD COLUMN title_en TEXT",
                "ALTER TABLE articles ADD COLUMN summary TEXT"):
        try:
            con.execute(ddl)
        except sqlite3.OperationalError:
            pass
    con.commit()
    return con


def fetch_candidates(con, limit=CANDIDATE_POOL):
    """RSSから未処理のニュースを収集する。"""
    items, seen = [], set()
    for feed_url in RSS_FEEDS:
        feed = feedparser.parse(feed_url)
        for e in feed.entries:
            link = getattr(e, "link", None)
            if not link or link in seen:
                continue
            seen.add(link)
            if con.execute("SELECT 1 FROM articles WHERE url=?", (link,)).fetchone():
                continue
            items.append({
                "title": e.title,
                "url": link,
                "summary": re.sub(r"<[^>]+>", "", getattr(e, "summary", "")),
            })
    return items[:limit]


def _extract_json(text):
    start, end = text.find("{"), text.rfind("}") + 1
    return json.loads(text[start:end])


def select_articles(candidates, limit=DAILY_LIMIT):
    """本命limit本+国際5本+ニッチ5本+面白ネタ1本をClaudeに選別させる。

    戻り値: {"main": [...], "intl": [...], "niche": [...], "fun": news or None}
    """
    if MOCK_MODE or len(candidates) <= limit:
        return {
            "main": candidates[:limit],
            "intl": candidates[limit:limit + INTL_LIMIT],
            "niche": candidates[limit + INTL_LIMIT:limit + INTL_LIMIT + NICHE_LIMIT],
            "fun": (candidates[limit + INTL_LIMIT + NICHE_LIMIT]
                    if len(candidates) > limit + INTL_LIMIT + NICHE_LIMIT else None),
        }
    import anthropic

    listing = "\n".join(f"{i}: {c['title']}" for i, c in enumerate(candidates))
    prompt = f"""あなたはまとめサイトの編集長です。以下のニュース一覧から4種類の枠を選定してください。
各枠に同じ番号を重複して入れてはいけません。

(1) "main": まとめサイトで最も閲覧数(クリック)が期待できる{limit}本
- 賛否が分かれ、コメント欄の議論が盛り上がりそうな話題
- 感情を動かす話題(驚き・怒り・共感・不安)
- 芸能人・著名人、お金、健康・医療、科学、事件・事故など大衆的関心の高い分野
- 同じ話題・同じジャンルばかりに偏らず、バランスを取ること
- 健康・医療・科学の話題があれば積極的に含めること
- 同一の出来事を扱う記事が複数ある場合は、最も情報量が多そうな1本だけを選ぶこと

(2) "intl": 海外の出来事・国際情勢のニュース{INTL_LIMIT}本
- 日本の読者にも興味を持たれやすい国際的な話題を優先

(3) "niche": 大衆向けではないが特定の層に強く刺さるニッチな話題{NICHE_LIMIT}本
- ジャンル不問(マニアックな技術・業界内ニュース・地方の話題・専門分野など)
- 検索で調べる人が確実にいそうなもの

(4) "fun": 思わず笑える・ほっこりする・変わったニュース1本

{listing}

JSONのみ出力: {{"main": [番号, ...], "intl": [番号, ...], "niche": [番号, ...], "fun": 番号}}
適した候補が無い枠は空配列または null"""
    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=MODEL, max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        data = _extract_json(msg.content[0].text)
        used = set()

        def take(key, n):
            out = []
            for i in data.get(key) or []:
                if isinstance(i, int) and 0 <= i < len(candidates) and i not in used:
                    used.add(i)
                    out.append(candidates[i])
                if len(out) >= n:
                    break
            return out

        main = take("main", limit)
        # mainの不足分は取得順で補完
        for i, c in enumerate(candidates):
            if len(main) >= limit:
                break
            if i not in used:
                used.add(i)
                main.append(c)
        intl = take("intl", INTL_LIMIT)
        niche = take("niche", NICHE_LIMIT)
        fun = None
        fi = data.get("fun")
        if isinstance(fi, int) and 0 <= fi < len(candidates) and fi not in used:
            fun = candidates[fi]
        return {"main": main, "intl": intl, "niche": niche, "fun": fun}
    except Exception as ex:
        print(f"話題性フィルタに失敗、取得順で継続: {ex}", file=sys.stderr)
        return {"main": candidates[:limit], "intl": [], "niche": [], "fun": None}


def select_fun_only(candidates):
    """面白ネタ1本だけを選ぶ(午後の不定期投稿用)。"""
    if not candidates:
        return None
    if MOCK_MODE:
        return candidates[0]
    import anthropic

    listing = "\n".join(f"{i}: {c['title']}" for i, c in enumerate(candidates))
    prompt = f"""以下のニュース一覧から、まとめサイトの「面白ネタ・コラム」枠に最も適した
1本を選んでください(思わず笑える・ほっこりする・変わった話題)。
{listing}

番号のみをJSONで出力: {{"fun": 番号}} 適した候補が無ければ {{"fun": null}}"""
    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=MODEL, max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        fi = _extract_json(msg.content[0].text).get("fun")
        if isinstance(fi, int) and 0 <= fi < len(candidates):
            return candidates[fi]
    except Exception as ex:
        print(f"面白ネタ選定に失敗: {ex}", file=sys.stderr)
    return None


def load_focus_hint():
    if FOCUS_HINT_FILE.exists():
        hint = FOCUS_HINT_FILE.read_text(encoding="utf-8").strip()
        if hint:
            return f"【編集方針】{hint}\nこの方針に合わせてタイトルとコメントの傾向を寄せてください。"
    return ""


def generate_article_mock(news, fun=False):
    """モックモード: API呼び出しなしでパイプラインを検証するためのダミー生成。"""
    base_ja = [
        "これはデカいニュースだな", "マジかよ、朝から驚いた",
        ">>1 デカいというか今後どうなるかだな", "詳細まだ出てないのか?",
        ">>2 自分も同じ反応だったわ", "冷静に考えるとそこまで意外でもない",
        ">>6 いやいや、十分意外だろ", "ソース見てきたけど続報待ちっぽい",
        "ちなみに過去にも似た事例あったよな", ">>9 あれとは規模が違う気がする",
    ]
    base_en = [
        "This is huge news", "No way, what a surprise",
        ">>1 Huge, but the real question is what happens next", "Any details yet?",
        ">>2 Same reaction here", "Honestly not that surprising if you think about it",
        ">>6 Nah, this is plenty surprising", "Checked the source, waiting for updates",
        "There was a similar case before btw", ">>9 Different scale though",
    ]
    n = COMMENTS_PER_ARTICLE
    return {
        "catchy_title": ("【コラム】" if fun else "【話題】") + news["title"],
        "category": "コラム" if fun else "国内",
        "summary": (news["summary"] or news["title"])[:200]
        + "(※モックモード: 実運用ではClaudeが独自要約を生成します)",
        "points": ["論点1(モック)", "論点2(モック)", "論点3(モック)"],
        "comments": [base_ja[i % len(base_ja)] for i in range(n)],
        "en": {
            "catchy_title": "[Mock] " + news["title"],
            "summary": "Mock English summary.",
            "points": ["Point 1 (mock)", "Point 2 (mock)", "Point 3 (mock)"],
            "comments": [base_en[i % len(base_en)] for i in range(n)],
        },
    }


def generate_article_api(news, focus_hint="", fun=False):
    import anthropic

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL,
        max_tokens=9000,
        messages=[{
            "role": "user",
            "content": PROMPT.format(
                tone=TONE_FUN if fun else "",
                title=news["title"],
                summary=news["summary"],
                url=news["url"],
                focus_hint=focus_hint,
                n_comments=COMMENTS_PER_ARTICLE,
            ),
        }],
    )
    return _extract_json(msg.content[0].text)


def generate_article(news, focus_hint="", fun=False):
    if MOCK_MODE:
        return generate_article_mock(news, fun)
    return generate_article_api(news, focus_hint, fun)


def related_articles(con, category, lang):
    """同カテゴリの直近記事(回遊率向上のため記事下に表示)。"""
    if lang == "en":
        rows = con.execute(
            "SELECT slug, title_en FROM articles WHERE category=? AND has_en=1 "
            "ORDER BY id DESC LIMIT 5", (category,)).fetchall()
    else:
        rows = con.execute(
            "SELECT slug, title FROM articles WHERE category=? "
            "ORDER BY id DESC LIMIT 5", (category,)).fetchall()
    return [{"slug": r[0], "title": r[1]} for r in rows if r[1]]


def write_article_pages(con, env, art, news, today, slug):
    """日本語ページと(あれば)英語ページを書き出す。has_enを返す。"""
    tpl = env.get_template("article.html")
    en = art.get("en") or {}
    has_en = bool(en.get("catchy_title") and en.get("comments"))

    html = tpl.render(
        art=art, source=news, date=today, t=T["ja"], lang="ja",
        alt_url=f"en/{slug}.html" if has_en else None,
        related=related_articles(con, art["category"], "ja"))
    (SITE / f"{slug}.html").write_text(html, encoding="utf-8")

    if has_en:
        (SITE / "en").mkdir(exist_ok=True)
        en_art = {
            "catchy_title": en["catchy_title"],
            "category": art["category"],
            "summary": en.get("summary", ""),
            "points": en.get("points", []),
            "comments": en["comments"],
        }
        html = tpl.render(
            art=en_art, source=news, date=today, t=T["en"], lang="en",
            alt_url=f"../{slug}.html",
            related=related_articles(con, art["category"], "en"))
        (SITE / "en" / f"{slug}.html").write_text(html, encoding="utf-8")
    return has_en


def render_site(con, env):
    tpl = env.get_template("index.html")
    cats_ja = [{"key": c["key"], "label": c["ja"]} for c in CATEGORIES]
    cats_en = [{"key": c["key"], "label": c["en"]} for c in CATEGORIES]

    rows = con.execute(
        "SELECT slug, title, category, created FROM articles ORDER BY id DESC LIMIT 100"
    ).fetchall()
    articles = [{"slug": r[0], "title": r[1], "category": r[2], "created": r[3]}
                for r in rows]
    html = tpl.render(articles=articles, t=T["ja"], lang="ja",
                      alt_url="en/index.html", cats=cats_ja)
    (SITE / "index.html").write_text(html, encoding="utf-8")

    rows = con.execute(
        "SELECT slug, title_en, category, created FROM articles WHERE has_en=1 "
        "ORDER BY id DESC LIMIT 100").fetchall()
    articles_en = [{"slug": r[0], "title": r[1], "category": r[2], "created": r[3]}
                   for r in rows if r[1]]
    (SITE / "en").mkdir(exist_ok=True)
    html = tpl.render(articles=articles_en, t=T["en"], lang="en",
                      alt_url="../index.html", cats=cats_en)
    (SITE / "en" / "index.html").write_text(html, encoding="utf-8")

    # リンク集ページ(相互リンクは links.json に {"name","url","desc"} で追加)
    links_file = BASE / "links.json"
    links = []
    if links_file.exists():
        try:
            links = json.loads(links_file.read_text(encoding="utf-8"))
        except Exception as ex:
            print(f"links.json 読み込み失敗: {ex}", file=sys.stderr)
    html = env.get_template("links.html").render(
        links=links, t=T["ja"], lang="ja", alt_url=None)
    (SITE / "links.html").write_text(html, encoding="utf-8")

    # ---- AI・機械閲覧用ページ群 ----
    render_machine_readable(con)

    # 検索エンジン向けサイトマップ(全記事対象)
    all_rows = con.execute(
        "SELECT slug, created, has_en FROM articles ORDER BY id DESC").fetchall()
    urls = [f"  <url><loc>{SITE_BASE_URL}</loc></url>",
            f"  <url><loc>{SITE_BASE_URL}en/index.html</loc></url>"]
    for r in all_rows:
        urls.append(f"  <url><loc>{SITE_BASE_URL}{r[0]}.html</loc><lastmod>{r[1]}</lastmod></url>")
        if r[2]:
            urls.append(f"  <url><loc>{SITE_BASE_URL}en/{r[0]}.html</loc><lastmod>{r[1]}</lastmod></url>")
    sitemap = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
               + "\n".join(urls) + "\n</urlset>\n")
    (SITE / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    (SITE / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {SITE_BASE_URL}sitemap.xml\n",
        encoding="utf-8")


def render_machine_readable(con):
    """AIエージェント・クローラー向けの機械可読ページを生成する。

    - llms.txt: LLM/AIエージェント向けのサイト案内(llmstxt.org準拠)
    - api/articles.json: 全記事メタデータのJSON API
    - rss.xml: RSS 2.0フィード(最新50件)
    """
    rows = con.execute(
        "SELECT slug, title, title_en, category, created, has_en, summary "
        "FROM articles ORDER BY id DESC").fetchall()

    # JSON API
    (SITE / "api").mkdir(exist_ok=True)
    items = [{
        "slug": r[0],
        "url": f"{SITE_BASE_URL}{r[0]}.html",
        "url_en": f"{SITE_BASE_URL}en/{r[0]}.html" if r[5] else None,
        "title": r[1],
        "title_en": r[2],
        "category": r[3],
        "published": r[4],
        "summary": r[6] or None,
    } for r in rows]
    api = {
        "site": "AIC通信",
        "description": "実際の日本のニュースを独自要約し、AI生成コメントを掲載するまとめサイト",
        "note": "全記事の要約とコメントはAIによる生成物です",
        "count": len(items),
        "articles": items,
    }
    (SITE / "api" / "articles.json").write_text(
        json.dumps(api, ensure_ascii=False, indent=1), encoding="utf-8")

    # RSS 2.0(最新50件)
    def rfc822(date_str):
        d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return d.strftime("%a, %d %b %Y 06:00:00 +0900")

    rss_items = []
    for r in rows[:50]:
        desc = (r[6] or "").replace("&", "&amp;").replace("<", "&lt;")
        title = r[1].replace("&", "&amp;").replace("<", "&lt;")
        rss_items.append(
            f"  <item>\n"
            f"    <title>{title}</title>\n"
            f"    <link>{SITE_BASE_URL}{r[0]}.html</link>\n"
            f"    <guid>{SITE_BASE_URL}{r[0]}.html</guid>\n"
            f"    <category>{r[3]}</category>\n"
            f"    <pubDate>{rfc822(r[4])}</pubDate>\n"
            f"    <description>{desc}</description>\n"
            f"  </item>")
    rss = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<rss version="2.0">\n<channel>\n'
           '  <title>AIC通信</title>\n'
           f'  <link>{SITE_BASE_URL}</link>\n'
           '  <description>実際のニュースにコメンテーターたちが反応するまとめサイト'
           '(要約・コメントはAI生成)</description>\n'
           '  <language>ja</language>\n'
           + "\n".join(rss_items) + "\n</channel>\n</rss>\n")
    (SITE / "rss.xml").write_text(rss, encoding="utf-8")

    # llms.txt(AIエージェント向けサイト案内)
    cats = ", ".join(c["key"] for c in CATEGORIES)
    llms = f"""# AIC通信

> 実際の日本のニュース(RSS)を題材に、AIが独自要約と掲示板風コメントを生成して
> 毎日約20本公開するまとめサイト。日本語・英語の2言語対応。
> 全記事の要約・コメント・タイトルはAI(Claude)による生成物であり、
> コメントは実在の人物の発言ではない。

## 機械可読リソース

- [記事一覧JSON API]({SITE_BASE_URL}api/articles.json): 全記事のメタデータ(タイトル・URL・カテゴリ・日付・要約)
- [RSSフィード]({SITE_BASE_URL}rss.xml): 最新50記事
- [サイトマップ]({SITE_BASE_URL}sitemap.xml): 全ページURL

## 主要ページ

- [トップページ(日本語)]({SITE_BASE_URL})
- [トップページ(英語)]({SITE_BASE_URL}en/index.html)
- [リンク集]({SITE_BASE_URL}links.html)

## メタ情報

- カテゴリ: {cats}
- 更新頻度: 毎日 6:00 / 18:00 JST(各約20本) + 15:00 JST(不定期コラム)
- 運営形態: 自動生成(Claude API + GitHub Actions)
- 記事引用時の注意: 要約はAI生成のため、一次情報は各記事内の出典リンクを参照すること
"""
    (SITE / "llms.txt").write_text(llms, encoding="utf-8")


def next_seq(con, today):
    return con.execute(
        "SELECT COUNT(*) FROM articles WHERE created=?", (today,)).fetchone()[0]


def insert_article(con, news, art, today, slug, has_en):
    con.execute(
        "INSERT INTO articles(url,title,category,created,slug,has_en,title_en,summary) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (news["url"], art["catchy_title"], art["category"], today, slug,
         1 if has_en else 0,
         (art.get("en") or {}).get("catchy_title"),
         art.get("summary", "")))
    con.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--column-only", action="store_true",
                        help="面白ネタ・コラム1本のみ生成(不定期の午後投稿用)")
    args = parser.parse_args()

    if MOCK_MODE:
        print("[!] ANTHROPIC_API_KEY 未設定のためモックモードで実行します")
    con = init_db()
    SITE.mkdir(exist_ok=True)
    env = Environment(loader=FileSystemLoader(BASE / "templates"))
    jst = datetime.timezone(datetime.timedelta(hours=9))
    today = datetime.datetime.now(jst).date().isoformat()

    if args.column_only:
        # 不定期性の演出: 約4割の確率で投稿しない(朝のコラム枠で最低1日1本は担保済み)
        if random.random() < 0.4:
            print("今回はスキップ(不定期投稿)")
            return
        candidates = fetch_candidates(con)
        news = select_fun_only(candidates)
        if not news:
            print("面白ネタ候補がありません")
            return
        plan = [(news, True, "")]
    else:
        candidates = fetch_candidates(con)
        if not candidates:
            print("新規ニュースがありません")
            render_site(con, env)
            return
        print(f"候補 {len(candidates)} 本から選別中...")
        sel = select_articles(candidates)
        focus_hint = load_focus_hint()
        plan = [(n, False, focus_hint if i == 0 else "")
                for i, n in enumerate(sel["main"])]
        plan += [(n, False, "この記事は国際ニュース枠です。categoryは必ず「国際」にすること。")
                 for n in sel["intl"]]
        plan += [(n, False, "この記事はニッチ枠です。その分野に詳しい読者にも読み応えが"
                            "あるよう、要約と論点は具体的に書くこと。")
                 for n in sel["niche"]]
        if sel["fun"]:
            plan.append((sel["fun"], True, ""))

    seq = next_seq(con, today)
    generated = 0
    for news, fun, hint in plan:
        try:
            art = generate_article(news, hint, fun)
        except Exception as ex:
            print(f"skip: {news['title']} ({ex})", file=sys.stderr)
            continue
        slug = f"{today}-{seq}"
        seq += 1
        has_en = write_article_pages(con, env, art, news, today, slug)
        insert_article(con, news, art, today, slug, has_en)
        generated += 1
        label = "[コラム]" if fun else ""
        print(f"generated: {slug}.html {label} {art['catchy_title']}"
              + (" (+en)" if has_en else ""))

    render_site(con, env)
    print(f"完了: {generated}本生成 / index.html 更新")


if __name__ == "__main__":
    main()
