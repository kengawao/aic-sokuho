# -*- coding: utf-8 -*-
"""GA4の人気記事データから翌日の編集方針(focus_hint.txt)を生成する。

GA4認証情報(GOOGLE_APPLICATION_CREDENTIALS)と ANTHROPIC_API_KEY が
未設定の場合は何もせず正常終了する(運用初期はこの状態で問題ない)。
"""
import os
import sys
import pathlib

BASE = pathlib.Path(__file__).parent
FOCUS_HINT_FILE = BASE / "focus_hint.txt"
GA4_PROPERTY = os.environ.get("GA4_PROPERTY_ID", "")  # 例: properties/123456789


def top_pages(n=10):
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        RunReportRequest, Dimension, Metric, DateRange,
    )

    client = BetaAnalyticsDataClient()
    req = RunReportRequest(
        property=GA4_PROPERTY,
        dimensions=[Dimension(name="pagePath"), Dimension(name="pageTitle")],
        metrics=[Metric(name="screenPageViews")],
        date_ranges=[DateRange(start_date="7daysAgo", end_date="yesterday")],
        limit=n,
    )
    return [
        (r.dimension_values[1].value, r.metric_values[0].value)
        for r in client.run_report(req).rows
    ]


def build_focus_hint():
    import anthropic

    pages = top_pages()
    if not pages:
        return None
    listing = "\n".join(f"- {title} ({pv}PV)" for title, pv in pages)
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": (
                f"以下は当サイトの直近7日間の人気記事です。\n{listing}\n\n"
                "共通する特徴(カテゴリ・タイトルの型・話題の傾向)を分析し、"
                "次に書くべき記事への指示を200字以内で出力してください。"
            ),
        }],
    )
    return msg.content[0].text.strip()


def main():
    if not GA4_PROPERTY or not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        print("GA4未設定のため人気分析をスキップします(focus_hintなしで生成)")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY未設定のため人気分析をスキップします")
        return
    try:
        hint = build_focus_hint()
    except Exception as ex:
        print(f"人気分析に失敗しました(スキップ): {ex}", file=sys.stderr)
        return
    if hint:
        FOCUS_HINT_FILE.write_text(hint, encoding="utf-8")
        print(f"編集方針を更新しました:\n{hint}")


if __name__ == "__main__":
    main()
