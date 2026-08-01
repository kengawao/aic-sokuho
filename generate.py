# -*- coding: utf-8 -*-
"""まとめサイト記事生成パイプライン。

RSSからニュースを取得し、Claude APIで要約とAIコメントを生成して
静的HTMLを site/ に出力する。

ANTHROPIC_API_KEY が未設定の場合はモックモードで動作し、
API呼び出しの代わりにサンプルデータで記事を生成する(動作確認用)。
"""
import datetime
import json
import os
import pathlib
import re
import sqlite3
import sys

import feedparser
from jinja2 import Environment, FileSystemLoader

BASE = pathlib.Path(__file__).parent
DB = BASE / "matome.db"
SITE = BASE / "site"
FOCUS_HINT_FILE = BASE / "focus_hint.txt"

DAILY_LIMIT = 10
COMMENTS_PER_ARTICLE = 30
MODEL = "claude-haiku-4-5-20251001"

RSS_FEEDS = [
    "https://news.yahoo.co.jp/rss/topics/top-picks.xml",
    "https://news.yahoo.co.jp/rss/topics/domestic.xml",
    "https://news.yahoo.co.jp/rss/topics/entertainment.xml",
    "https://news.yahoo.co.jp/rss/topics/it.xml",
    "https://www.nhk.or.jp/rss/news/cat0.xml",
]

MOCK_MODE = not os.environ.get("ANTHROPIC_API_KEY")

PROMPT = """あなたはまとめサイトの編集者です。以下のニュースを題材に記事を作成してください。

ニュース見出し: {title}
RSS概要: {summary}
出典URL: {url}
{focus_hint}

以下のJSON形式のみで出力してください:
{{
  "catchy_title": "クリックしたくなる記事タイトル(煽りすぎず、事実に反しない範囲で)",
  "category": "国内/経済/エンタメ/スポーツ/IT/国際 のいずれか",
  "summary": "ニュースの内容を自分の言葉で書いた200字程度の要約(原文のコピー禁止)",
  "points": ["論点1", "論点2", "論点3"],
  "comments": ["コメント本文", ...]
}}
commentsは{n_comments}件。匿名掲示板(5ちゃんねる)風のスレッドとして書くこと:
- 口語・短文中心。「これは草」「マジかよ」のようなネットスラングも適度に使う
- 全体の3〜4割は「>>5 それは違うだろ」のように >>レス番号 で
  先行コメントへ返信し、会話・議論が続いているように見せる
  (返信先は必ず自分より小さい番号にすること)
- 賛成派・反対派・冷静な分析派・ツッコミ役・雑学披露役など視点を分散させる
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
    con.commit()
    return con


def fetch_candidates(con, limit=DAILY_LIMIT):
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


def load_focus_hint():
    if FOCUS_HINT_FILE.exists():
        hint = FOCUS_HINT_FILE.read_text(encoding="utf-8").strip()
        if hint:
            return f"【編集方針】{hint}\nこの方針に合わせてタイトルとコメントの傾向を寄せてください。"
    return ""


def generate_article_mock(news):
    """モックモード: API呼び出しなしでパイプラインを検証するためのダミー生成。"""
    base = [
        "これはデカいニュースだな",
        "マジかよ、朝から驚いた",
        ">>1 デカいというか今後どうなるかだな",
        "詳細まだ出てないのか?",
        ">>2 自分も同じ反応だったわ",
        "冷静に考えるとそこまで意外でもない",
        ">>6 いやいや、十分意外だろ",
        "ソース見てきたけど続報待ちっぽい",
        "ちなみに過去にも似た事例あったよな",
        ">>9 あれとは規模が違う気がする",
    ]
    comments = [base[i % len(base)] for i in range(COMMENTS_PER_ARTICLE)]
    return {
        "catchy_title": f"【話題】{news['title']}",
        "category": "国内",
        "summary": (news["summary"] or news["title"]) [:200]
        + "(※モックモード: 実運用ではClaudeが独自要約を生成します)",
        "points": ["論点1(モック)", "論点2(モック)", "論点3(モック)"],
        "comments": comments,
    }


def generate_article_api(news, focus_hint=""):
    import anthropic

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        messages=[{
            "role": "user",
            "content": PROMPT.format(
                title=news["title"],
                summary=news["summary"],
                url=news["url"],
                focus_hint=focus_hint,
                n_comments=COMMENTS_PER_ARTICLE,
            ),
        }],
    )
    text = msg.content[0].text
    start, end = text.find("{"), text.rfind("}") + 1
    return json.loads(text[start:end])


def generate_article(news, focus_hint=""):
    if MOCK_MODE:
        return generate_article_mock(news)
    return generate_article_api(news, focus_hint)


def render_site(con, env):
    rows = con.execute(
        "SELECT slug, title, category, created FROM articles ORDER BY id DESC LIMIT 100"
    ).fetchall()
    articles = [
        {"slug": r[0], "title": r[1], "category": r[2], "created": r[3]}
        for r in rows
    ]
    html = env.get_template("index.html").render(articles=articles)
    (SITE / "index.html").write_text(html, encoding="utf-8")


def main():
    if MOCK_MODE:
        print("[!] ANTHROPIC_API_KEY 未設定のためモックモードで実行します")
    con = init_db()
    SITE.mkdir(exist_ok=True)
    env = Environment(loader=FileSystemLoader(BASE / "templates"))
    today = datetime.date.today().isoformat()
    focus_hint = load_focus_hint()

    candidates = fetch_candidates(con)
    if not candidates:
        print("新規ニュースがありません")
        render_site(con, env)
        return

    # 同日に複数回実行しても既存記事を上書きしないよう、連番は既存数から続ける
    seq = con.execute(
        "SELECT COUNT(*) FROM articles WHERE created=?", (today,)).fetchone()[0]

    generated = 0
    for i, news in enumerate(candidates):
        # 1本目のみ人気分析に基づく編集方針を適用
        hint = focus_hint if i == 0 else ""
        try:
            art = generate_article(news, hint)
        except Exception as ex:
            print(f"skip: {news['title']} ({ex})", file=sys.stderr)
            continue
        slug = f"{today}-{seq}"
        seq += 1
        html = env.get_template("article.html").render(
            art=art, source=news, date=today)
        (SITE / f"{slug}.html").write_text(html, encoding="utf-8")
        con.execute(
            "INSERT INTO articles(url,title,category,created,slug) VALUES(?,?,?,?,?)",
            (news["url"], art["catchy_title"], art["category"], today, slug),
        )
        con.commit()
        generated += 1
        print(f"generated: {slug}.html  {art['catchy_title']}")

    render_site(con, env)
    print(f"完了: {generated}本生成 / index.html 更新")


if __name__ == "__main__":
    main()
