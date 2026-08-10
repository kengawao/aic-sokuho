# -*- coding: utf-8 -*-
"""記事からニュース番組風ショート動画(縦型1080x1920 MP4)を生成する。

構図: 実際のニュース番組風
- 画面左下にペンフィールドのホムンクルス風キャスター
  (口パク・まばたき・体の揺れ・登場スライドイン)
- 背景ライトの流れ、テロップのスクロール、見出しのスライドインなど常時アニメーション
- 音声はキャスター口調の滑らかなナレーション、シーン間はフェードで転換

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

from PIL import Image, ImageDraw, ImageFont

BASE = pathlib.Path(__file__).parent
DB = BASE / "matome.db"
DOCS = BASE / "docs"
OUT = BASE / "shorts"

W, H = 1080, 1920
FPS = 12
VOICE = "ja-JP-KeitaNeural"
SITE_NAME = "AIC通信"

SKIN = (242, 201, 160)
SKIN_DARK = (203, 158, 118)
SKIN_LIGHT = (255, 228, 196)
LIP = (192, 57, 43)
LIP_DARK = (140, 35, 25)
SUIT = (30, 41, 82)
SUIT_LIGHT = (52, 68, 120)
BG_DARK = (10, 16, 40)
BEAM = (18, 27, 64)

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


def cut_at_sentence(text, limit):
    """limit以内で文の切れ目まで切り詰める(読み上げが不自然に途切れないように)。"""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    pos = max(cut.rfind("。"), cut.rfind("、"))
    return cut[:pos + 1] if pos > limit // 2 else cut


def clean_for_speech(text):
    """ネットスラングや記号を読み上げ向けに整える。"""
    text = re.sub(r"[wW]+$", "", text)
    text = re.sub(r"[「」『』()()>><<]", "", text)
    text = text.replace("…", "、").replace("!?", "。").replace("?", "?")
    return text.strip()


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


# ---------- ホムンクルスキャスター ----------

def draw_homunculus(mouth_open, blink):
    c = Image.new("RGBA", (460, 560), (0, 0, 0, 0))
    d = ImageDraw.Draw(c)

    d.polygon([(80, 560), (380, 560), (350, 360), (110, 360)], fill=SUIT)
    d.polygon([(110, 360), (350, 360), (330, 340), (130, 340)], fill=SUIT_LIGHT)
    d.polygon([(200, 360), (260, 360), (230, 470)], fill=(245, 245, 250))
    d.polygon([(220, 365), (240, 365), (245, 430), (230, 460), (215, 430)],
              fill=(180, 30, 40))

    d.ellipse([120, 60, 340, 300], fill=SKIN, outline=SKIN_DARK, width=4)
    d.ellipse([150, 230, 310, 300], fill=SKIN_DARK)
    d.ellipse([150, 80, 250, 150], fill=SKIN_LIGHT)

    for x0, x1 in [(85, 140), (320, 375)]:
        d.ellipse([x0, 130, x1, 230], fill=SKIN, outline=SKIN_DARK, width=3)

    for ex in (195, 265):
        if blink:
            d.line([(ex - 12, 152), (ex + 12, 152)], fill=(40, 40, 40), width=5)
        else:
            d.ellipse([ex - 12, 140, ex + 12, 164], fill=(255, 255, 255))
            d.ellipse([ex - 6, 146, ex + 6, 158], fill=(40, 40, 40))
        d.line([(ex - 16, 126), (ex + 16, 122)], fill=(90, 60, 40), width=6)

    if mouth_open:
        d.ellipse([160, 185, 300, 285], fill=LIP, outline=LIP_DARK, width=5)
        d.ellipse([190, 215, 270, 265], fill=(70, 20, 15))
        d.ellipse([200, 250, 260, 270], fill=(230, 120, 110))
    else:
        d.ellipse([160, 200, 300, 275], fill=LIP, outline=LIP_DARK, width=5)
        d.line([(175, 237), (285, 237)], fill=LIP_DARK, width=7)
    d.ellipse([180, 205, 240, 230], fill=(220, 110, 100))

    for side in (-1, 1):
        cx = 230 + side * 195
        d.ellipse([cx - 70, 300, cx + 70, 470], fill=SKIN, outline=SKIN_DARK, width=4)
        for i in range(5):
            fx = cx - 56 + i * 28
            d.rounded_rectangle([fx, 245 + abs(i - 2) * 14, fx + 22, 330],
                                radius=11, fill=SKIN, outline=SKIN_DARK, width=3)
        d.ellipse([cx - 50, 320, cx + 10, 380], fill=SKIN_LIGHT)

    return c


# ---------- 背景・オーバーレイのレイヤー生成 ----------

def make_beam_tile():
    """横に流れるライトビームのタイル(ループ用に周期240pxで描く)。"""
    tile = Image.new("RGBA", (W + 240, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    for i in range(-H // 3, W + 480, 240):
        d.polygon([(i, 0), (i + 90, 0), (i - H // 3 + 90, H), (i - H // 3, H)],
                  fill=BEAM + (255,))
    return tile


def make_text_layer(big_text):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    f_big = load_font(110)
    lines = wrap(big_text, 8)
    if len(lines) > 3:
        lines = lines[:3]
        lines[2] = lines[2][:6] + "…"
    y = 400
    for ln in lines:
        tw = d.textlength(ln, font=f_big)
        d.text(((W - tw) / 2, y), ln, font=f_big, fill=(255, 210, 70),
               stroke_width=6, stroke_fill=(10, 10, 30))
        y += 150
    return layer


def make_header(live_bright):
    layer = Image.new("RGBA", (W, 130), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rectangle([0, 0, W, 130], fill=(8, 12, 30, 255))
    d.rounded_rectangle([40, 28, 360, 102], radius=14, fill=(37, 99, 235))
    d.text((70, 40), f"{SITE_NAME} NEWS", font=load_font(46), fill=(255, 255, 255))
    red = (220, 30, 45) if live_bright else (120, 25, 35)
    d.rounded_rectangle([W - 220, 32, W - 40, 98], radius=12, fill=red)
    d.text((W - 190, 42), "LIVE", font=load_font(42), fill=(255, 255, 255))
    return layer


def make_lower_third(headline):
    layer = Image.new("RGBA", (W, 170), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rectangle([0, 0, W, 170], fill=(235, 238, 245, 255))
    d.rectangle([0, 0, W, 30], fill=(200, 30, 40, 255))
    f_head = load_font(44)
    hy = 46
    for ln in wrap(headline, 22)[:2]:
        d.text((40, hy), ln, font=f_head, fill=(20, 25, 45))
        hy += 58
    return layer


def make_ticker_text():
    msg = f"チャンネル登録で毎日ニュースをお届け   {SITE_NAME} で検索   "
    f = load_font(36)
    tmp = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    tw = int(tmp.textlength(msg, font=f))
    layer = Image.new("RGBA", (tw * 2, 60), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text((0, 6), msg * 2, font=f, fill=(150, 170, 220))
    return layer, tw


def with_alpha(layer, factor):
    if factor >= 1.0:
        return layer
    out = layer.copy()
    out.putalpha(out.split()[3].point(lambda a: int(a * factor)))
    return out


async def tts(text, path):
    import edge_tts
    await edge_tts.Communicate(text, VOICE).save(str(path))


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

    comment = clean_for_speech(art["comments"][0]) if art["comments"] else ""
    p = [cut_at_sentence(x, 60) for x in art["points"]]
    points_speech = ""
    if p:
        heads = ["まず、", "次に、", "そして、"]
        points_speech = "".join(h + x.rstrip("。") + "。" for h, x in zip(heads, p))
    summary_speech = cut_at_sentence(art["summary"], 170)

    segments = [
        ("本日の注目ニュース",
         f"こんにちは。{SITE_NAME}ニュースです。きょうの注目ニュースをお伝えします。"),
        (art["title"],
         f"{art['title']}。{summary_speech}"),
        ("ここがポイント",
         f"続いて、注目のポイントです。{points_speech}"),
        ("ネットの反応は?",
         f"この話題に、ネットではさまざまな声が寄せられています。たとえば、"
         f"{cut_at_sentence(comment, 70)}、という意見です。"),
        (SITE_NAME + " で検索",
         f"より詳しい解説は、{SITE_NAME}のウェブサイトでご覧ください。"
         "以上、ホムンクルス解説員がお伝えしました。"),
    ]

    # レイヤーを事前生成
    chars = {(m, b): draw_homunculus(m, b) for m in (True, False) for b in (True, False)}
    beams = make_beam_tile()
    headers = {True: make_header(True), False: make_header(False)}
    lower = make_lower_third(art["title"])
    ticker, ticker_w = make_ticker_text()
    label_font = load_font(30)

    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        segs = []
        gframe = 0  # 全シーン通しのフレーム番号(背景を連続的に流すため)
        for si, (big_text, narration) in enumerate(segments):
            mp3 = td / f"a{si}.mp3"
            asyncio.run(tts(narration, mp3))
            d_sec = dur(mp3) + 0.25
            n_frames = max(int(d_sec * FPS), 1)
            text_layer = make_text_layer(big_text)

            fdir = td / f"f{si}"
            fdir.mkdir()
            for i in range(n_frames):
                frame = Image.new("RGB", (W, H), BG_DARK)
                # 背景ビームが常に右へ流れる
                bx = -(gframe * 2 % 240)
                frame.paste(beams, (bx, 0), beams)

                # 中央テキスト: フェード+下からスライドイン
                a = min(1.0, i / 7)
                frame.paste(with_alpha(text_layer, a), (0, int((1 - a) * 50)),
                            with_alpha(text_layer, a))

                # ヘッダー(LIVEバッジ点滅)
                hdr = headers[(gframe // 6) % 2 == 0]
                frame.paste(hdr, (0, 0), hdr)

                # 下部バー: 右からスライドイン(各シーン冒頭)
                lx = int(max(0, 7 - i) ** 2 * 8)
                frame.paste(lower, (lx, 1660), lower)

                # スクロールテロップ
                tx = -(gframe * 5 % ticker_w)
                frame.paste(ticker, (tx, 1848), ticker)

                # キャスター: 口パク+まばたき+ゆらぎ回転+シーン1で登場スライド
                talking = i < n_frames - int(0.3 * FPS)
                mouth = talking and (i // 3) % 2 == 0
                blink = (gframe % (FPS * 3)) < 2
                fig = chars[(mouth, blink)]
                sway = 2.5 * math.sin(gframe / 8)
                fig = fig.rotate(sway, resample=Image.BICUBIC)
                bob = int(6 * math.sin(gframe / 2.5))
                if si == 0:
                    ease = min(1.0, i / 10)
                    cx = int(-350 + 370 * (1 - (1 - ease) ** 3))
                else:
                    cx = 20
                frame.paste(fig, (cx, 1100 + bob), fig)
                fd = ImageDraw.Draw(frame)
                fd.rounded_rectangle([30, 1615, 430, 1657], radius=8,
                                     fill=(37, 99, 235))
                fd.text((52, 1620), "ホムンクルス解説員(AI)", font=label_font,
                        fill=(255, 255, 255))
                frame.save(fdir / f"{i:04d}.png")
                gframe += 1

            seg = td / f"s{si}.mp4"
            fade_out = max(d_sec - 0.3, 0)
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-framerate", str(FPS),
                 "-i", str(fdir / "%04d.png"), "-i", str(mp3),
                 "-vf", f"fade=t=in:st=0:d=0.25,fade=t=out:st={fade_out:.2f}:d=0.3",
                 "-af", f"afade=t=in:st=0:d=0.12,afade=t=out:st={max(d_sec-0.25,0):.2f}:d=0.25",
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
