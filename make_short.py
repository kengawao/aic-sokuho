# -*- coding: utf-8 -*-
"""記事からショート動画(縦型1080x1920 MP4)を生成する。

YouTube Shorts / TikTok / Instagram Reels 向け。
構成: タイトル → 要約 → ポイント → コメント抜粋 → サイト誘導
音声は edge-tts(無料のMicrosoft音声合成)によるナレーション付き。

使い方:
  python make_short.py                # 今日の最初の記事で作成
  python make_short.py 2026-08-04-3   # slug指定で作成

出力: shorts/{slug}.mp4
必要: ffmpeg(PATH上), pip install edge-tts pillow
"""
import asyncio
import datetime
import json
import pathlib
import re
import sqlite3
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

BASE = pathlib.Path(__file__).parent
DB = BASE / "matome.db"
DOCS = BASE / "docs"
OUT = BASE / "shorts"

W, H = 1080, 1920
VOICE = "ja-JP-NanamiNeural"
SITE_NAME = "AIC通信"
ACCENT = (43, 108, 176)
BG = (18, 24, 38)
FG = (245, 247, 250)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\YuGothB.ttc",
    r"C:\Windows\Fonts\meiryob.ttc",
    r"C:\Windows\Fonts\msgothic.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]


def load_font(size):
    for p in FONT_CANDIDATES:
        if pathlib.Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def pick_slug():
    con = sqlite3.connect(DB)
    jst = datetime.timezone(datetime.timedelta(hours=9))
    today = datetime.datetime.now(jst).date().isoformat()
    row = con.execute(
        "SELECT slug FROM articles WHERE created=? ORDER BY id LIMIT 1",
        (today,)).fetchone()
    if not row:
        row = con.execute("SELECT slug FROM articles ORDER BY id DESC LIMIT 1").fetchone()
    return row[0] if row else None


def parse_article(slug):
    """生成済みHTMLからタイトル・要約・ポイント・コメントを抽出する。"""
    html = (DOCS / f"{slug}.html").read_text(encoding="utf-8")
    title = re.search(r"<h1>(.*?)</h1>", html, re.S).group(1).strip()
    summary = re.search(r'<section class="summary"[^>]*>\s*<p[^>]*>(.*?)</p>', html, re.S).group(1).strip()
    points = re.findall(
        r"<li>(.*?)</li>",
        re.search(r'<ul class="points">(.*?)</ul>', html, re.S).group(1), re.S)
    comments = re.findall(r"<p>(.*?)</p>", re.search(
        r'<section class="comments">(.*?)</section>', html, re.S).group(1), re.S)
    clean = lambda s: re.sub(r"<[^>]+>", "", s).strip()
    return {
        "title": clean(title),
        "summary": clean(summary),
        "points": [clean(p) for p in points][:3],
        "comments": [clean(c) for c in comments if not clean(c).startswith(">>")][:2],
    }


def wrap(text, per_line):
    lines, cur = [], ""
    for ch in text:
        cur += ch
        if len(cur) >= per_line:
            lines.append(cur)
            cur = ""
    if cur:
        lines.append(cur)
    return lines


def make_slide(path, heading, body_lines, page, total):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 160], fill=ACCENT)
    d.text((40, 45), SITE_NAME, font=load_font(64), fill=(255, 255, 255))
    d.text((W - 220, 60), f"{page}/{total}", font=load_font(44), fill=(255, 255, 255))
    f_head = load_font(72)
    f_body = load_font(58)
    y = 320
    for ln in wrap(heading, 13):
        d.text((60, y), ln, font=f_head, fill=(255, 210, 90))
        y += 100
    y += 40
    for ln in body_lines:
        d.text((60, y), ln, font=f_body, fill=FG)
        y += 88
        if y > H - 260:
            break
    d.text((60, H - 150), "続きは AIC通信 で(プロフィールのリンクから)",
           font=load_font(46), fill=(160, 200, 255))
    img.save(path)


async def tts(text, path):
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate="+8%").save(str(path))


def dur(path):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "json", str(path)], capture_output=True, text=True)
    return float(json.loads(out.stdout)["format"]["duration"])


def main():
    slug = sys.argv[1] if len(sys.argv) > 1 else pick_slug()
    if not slug:
        print("記事がありません")
        return
    art = parse_article(slug)
    OUT.mkdir(exist_ok=True)

    slides = [
        ("今日の話題", wrap(art["title"], 16), art["title"]),
        ("何があった?", wrap(art["summary"][:140], 16), art["summary"][:200]),
        ("ここがポイント", sum([wrap("・" + p, 16) for p in art["points"]], []),
         "ポイントは、" + "。".join(art["points"])),
    ]
    for i, c in enumerate(art["comments"]):
        slides.append((f"みんなの反応 {i + 1}", wrap("「" + c + "」", 15), c))
    slides.append(("チャンネル登録・フォローお願いします",
                   wrap("最新ニュースのまとめを毎日配信中!", 15),
                   "この続きと他のニュースは、AIC通信でチェックしてください。"))

    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        segs = []
        for i, (head, body, narration) in enumerate(slides):
            png = td / f"s{i}.png"
            mp3 = td / f"s{i}.mp3"
            seg = td / f"s{i}.mp4"
            make_slide(png, head, body, i + 1, len(slides))
            asyncio.run(tts(narration, mp3))
            d = dur(mp3) + 0.5
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(png),
                 "-i", str(mp3), "-t", f"{d:.2f}", "-c:v", "libx264",
                 "-tune", "stillimage", "-pix_fmt", "yuv420p", "-r", "30",
                 "-c:a", "aac", str(seg)],
                check=True)
            segs.append(seg)
            print(f"slide {i + 1}/{len(slides)} ({d:.1f}s)")
        concat = td / "list.txt"
        concat.write_text("\n".join(f"file '{s.as_posix()}'" for s in segs),
                          encoding="utf-8")
        out = OUT / f"{slug}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
             "-i", str(concat), "-c", "copy", str(out)], check=True)
    total = dur(out)
    print(f"完成: {out} ({total:.0f}秒)")


if __name__ == "__main__":
    main()
