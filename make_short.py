# -*- coding: utf-8 -*-
"""記事からニュース番組風ショート動画(縦型1080x1920 MP4)を生成する。

構図: 実際のニュース番組風
- 画面左下にペンフィールドのホムンクルス風キャスター(疑似3D・口パクアニメ)
- 画面上部に大きなキーワードのみ(文字最小限)
- 画面下部にニュース番組風のヘッドラインバー
- 音声ナレーション(キャスター口調)主体で進行

使い方:
  python make_short.py                # 今日の最初の記事で作成
  python make_short.py 2026-08-10-3   # slug指定で作成

出力: shorts/{slug}.mp4
必要: ffmpeg(PATH上), pip install edge-tts pillow
"""
import asyncio
import datetime
import json
import math
import pathlib
import re
import sqlite3
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont

BASE = pathlib.Path(__file__).parent
DB = BASE / "matome.db"
DOCS = BASE / "docs"
OUT = BASE / "shorts"

W, H = 1080, 1920
FPS = 12
VOICE = "ja-JP-KeitaNeural"  # キャスターらしい男性音声
SITE_NAME = "AIC通信"

SKIN = (242, 201, 160)
SKIN_DARK = (203, 158, 118)
SKIN_LIGHT = (255, 228, 196)
LIP = (192, 57, 43)
LIP_DARK = (140, 35, 25)
SUIT = (30, 41, 82)
SUIT_LIGHT = (52, 68, 120)

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
    html = (DOCS / f"{slug}.html").read_text(encoding="utf-8")
    clean = lambda s: re.sub(r"<[^>]+>", "", s).strip()
    title = clean(re.search(r"<h1>(.*?)</h1>", html, re.S).group(1))
    summary = clean(re.search(
        r'<section class="summary"[^>]*>\s*<p[^>]*>(.*?)</p>', html, re.S).group(1))
    points = [clean(p) for p in re.findall(
        r"<li>(.*?)</li>",
        re.search(r'<ul class="points">(.*?)</ul>', html, re.S).group(1), re.S)]
    comments = [clean(c) for c in re.findall(r"<p>(.*?)</p>", re.search(
        r'<section class="comments">(.*?)</section>', html, re.S).group(1), re.S)]
    comments = [c for c in comments if not c.startswith(">>")]
    return {"title": title, "summary": summary,
            "points": points[:3], "comments": comments[:2]}


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


# ---------- ホムンクルスキャスター(疑似3D) ----------

def draw_homunculus(mouth_open):
    """ペンフィールドのホムンクルス風キャスター(巨大な手と唇)を描く。"""
    c = Image.new("RGBA", (460, 560), (0, 0, 0, 0))
    d = ImageDraw.Draw(c)

    # スーツの胴体(グラデーション風に2段)
    d.polygon([(80, 560), (380, 560), (350, 360), (110, 360)], fill=SUIT)
    d.polygon([(110, 360), (350, 360), (330, 340), (130, 340)], fill=SUIT_LIGHT)
    # シャツとネクタイ
    d.polygon([(200, 360), (260, 360), (230, 470)], fill=(245, 245, 250))
    d.polygon([(220, 365), (240, 365), (245, 430), (230, 460), (215, 430)],
              fill=(180, 30, 40))

    # 頭(立体感: ベース→影→ハイライト)
    d.ellipse([120, 60, 340, 300], fill=SKIN, outline=SKIN_DARK, width=4)
    d.ellipse([150, 230, 310, 300], fill=SKIN_DARK)   # あご下の影
    d.ellipse([150, 80, 250, 150], fill=SKIN_LIGHT)   # 額のハイライト

    # 大きな耳(ホムンクルスの特徴)
    for x0, x1 in [(85, 140), (320, 375)]:
        d.ellipse([x0, 130, x1, 230], fill=SKIN, outline=SKIN_DARK, width=3)

    # 目(小さめ=誇張対比)と眉
    for ex in (195, 265):
        d.ellipse([ex - 12, 140, ex + 12, 164], fill=(255, 255, 255))
        d.ellipse([ex - 6, 146, ex + 6, 158], fill=(40, 40, 40))
        d.line([(ex - 16, 126), (ex + 16, 122)], fill=(90, 60, 40), width=6)

    # 巨大な唇(最大の特徴)
    if mouth_open:
        d.ellipse([160, 185, 300, 285], fill=LIP, outline=LIP_DARK, width=5)
        d.ellipse([190, 215, 270, 265], fill=(70, 20, 15))   # 開いた口
        d.ellipse([200, 250, 260, 270], fill=(230, 120, 110))  # 舌
    else:
        d.ellipse([160, 200, 300, 275], fill=LIP, outline=LIP_DARK, width=5)
        d.line([(175, 237), (285, 237)], fill=LIP_DARK, width=7)
    d.ellipse([180, 205, 240, 230], fill=(220, 110, 100))  # 唇ハイライト

    # 巨大な手(左右、指5本)
    for side in (-1, 1):
        cx = 230 + side * 195
        hand = [cx - 70, 300, cx + 70, 470]
        d.ellipse(hand, fill=SKIN, outline=SKIN_DARK, width=4)
        for i in range(5):
            fx = cx - 56 + i * 28
            d.rounded_rectangle([fx, 245 + abs(i - 2) * 14, fx + 22, 330],
                                radius=11, fill=SKIN, outline=SKIN_DARK, width=3)
        d.ellipse([cx - 50, 320, cx + 10, 380], fill=SKIN_LIGHT)  # 手のひらハイライト

    return c


def scene_bg(big_text, headline, badge="LIVE"):
    """ニューススタジオ風の背景+最小限のテキスト。"""
    img = Image.new("RGB", (W, H), (10, 16, 40))
    d = ImageDraw.Draw(img)
    # スタジオ背景: 斜めのライトビーム
    for i in range(0, W + H, 160):
        d.polygon([(i, 0), (i + 70, 0), (i - H // 3 + 70, H), (i - H // 3, H)],
                  fill=(16, 24, 58))
    d.rectangle([0, 0, W, 130], fill=(8, 12, 30))
    # 局ロゴ
    d.rounded_rectangle([40, 28, 360, 102], radius=14, fill=(37, 99, 235))
    d.text((70, 40), f"{SITE_NAME} NEWS", font=load_font(46), fill=(255, 255, 255))
    # LIVEバッジ
    d.rounded_rectangle([W - 220, 32, W - 40, 98], radius=12, fill=(200, 30, 40))
    d.text((W - 190, 42), badge, font=load_font(42), fill=(255, 255, 255))

    # 中央の大きなキーワード(文字は最小限)
    f_big = load_font(110)
    y = 420
    lines = wrap(big_text, 8)
    if len(lines) > 3:
        lines = lines[:3]
        lines[2] = lines[2][:6] + "…"
    for ln in lines:
        tw = d.textlength(ln, font=f_big)
        d.text(((W - tw) / 2, y), ln, font=f_big, fill=(255, 210, 70),
               stroke_width=6, stroke_fill=(10, 10, 30))
        y += 150

    # 下部: ニュース番組風ヘッドラインバー
    d.rectangle([0, 1660, W, 1830], fill=(235, 238, 245))
    d.rectangle([0, 1660, W, 1690], fill=(200, 30, 40))
    f_head = load_font(44)
    hy = 1706
    for ln in wrap(headline, 22)[:2]:
        d.text((40, hy), ln, font=f_head, fill=(20, 25, 45))
        hy += 58
    d.rectangle([0, 1830, W, H], fill=(8, 12, 30))
    d.text((40, 1850), "チャンネル登録で毎日ニュースをお届け",
           font=load_font(36), fill=(150, 170, 220))
    return img


async def tts(text, path):
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate="+6%").save(str(path))


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

    comment = art["comments"][0] if art["comments"] else ""
    points_speech = "。".join(art["points"])
    segments = [
        ("本日の注目ニュース",
         f"こんにちは、{SITE_NAME}ニュースの時間です。本日の注目はこちらです。"),
        (art["title"],
         f"{art['title']}。{art['summary'][:160]}"),
        ("ここがポイント",
         f"ポイントを整理します。{points_speech}。"),
        ("ネットの反応は?",
         f"ネットでは、{comment[:80]}、といった声が上がっています。"),
        (SITE_NAME + " で検索",
         f"より詳しい解説とコメントは、{SITE_NAME}のサイトでご覧いただけます。"
         "以上、ホムンクルス解説員がお伝えしました。"),
    ]

    homunculus = {True: draw_homunculus(True), False: draw_homunculus(False)}
    label_font = load_font(30)

    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        segs = []
        for si, (big_text, narration) in enumerate(segments):
            mp3 = td / f"a{si}.mp3"
            asyncio.run(tts(narration, mp3))
            d_sec = dur(mp3) + 0.4
            n_frames = max(int(d_sec * FPS), 1)
            bg = scene_bg(big_text, art["title"])

            fdir = td / f"f{si}"
            fdir.mkdir()
            for i in range(n_frames):
                frame = bg.copy()
                # 口パク(0.25秒周期)+ゆったりした縦揺れで「話している」動きを出す
                talking = i < n_frames - int(0.4 * FPS)
                mouth = talking and (i // 3) % 2 == 0
                bob = int(6 * math.sin(i / 2.5))
                fig = homunculus[mouth]
                frame.paste(fig, (20, 1100 + bob), fig)
                fd = ImageDraw.Draw(frame)
                fd.rounded_rectangle([30, 1615, 430, 1657], radius=8,
                                     fill=(37, 99, 235))
                fd.text((52, 1620), "ホムンクルス解説員(AI)", font=label_font,
                        fill=(255, 255, 255))
                frame.save(fdir / f"{i:04d}.png")

            seg = td / f"s{si}.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-framerate", str(FPS),
                 "-i", str(fdir / "%04d.png"), "-i", str(mp3),
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
                 "-c:a", "aac", "-t", f"{d_sec:.2f}", str(seg)],
                check=True)
            segs.append(seg)
            print(f"scene {si + 1}/{len(segments)} ({d_sec:.1f}s, {n_frames}frames)")

        concat = td / "list.txt"
        concat.write_text("\n".join(f"file '{s.as_posix()}'" for s in segs),
                          encoding="utf-8")
        out = OUT / f"{slug}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
             "-i", str(concat), "-c", "copy", str(out)], check=True)
    print(f"完成: {out} ({dur(out):.0f}秒)")


if __name__ == "__main__":
    main()
