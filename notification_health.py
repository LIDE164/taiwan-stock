"""Deduplicated operational alert, separate from prices and ranking state.

Fail closed if Firestore is unavailable: GitHub's failed job remains visible,
but we never bypass the delivery ledger and spam every backup schedule.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from importlib import import_module
import logging
import os
import re

from research_delivery import FirestoreReportStore, deliver_report, telegram_send_text

TPE = timezone(timedelta(hours=8))
FORMAT = "prediction_health_v1"


def build_health_message(*, now, analysis_date="", repository="", run_id=""):
    local = now.astimezone(TPE)
    message = ["⚠️ 預測名單行情通知未完成", local.strftime("實際執行時間：%Y-%m-%d %H:%M（台北）"),
               "可能為排程錯過交易時段、榜單尚未更新或行情／寄送失敗，請查看下方執行紀錄。"]
    try:
        parsed = date.fromisoformat(str(analysis_date))
        if parsed.isoformat() == analysis_date:
            message.append(f"目前保存的榜單分析日：{analysis_date}（不代表今日名單已完成）")
    except (TypeError, ValueError):
        pass
    message.extend(["不會把舊名單改標今日，也不會用現在價格補造錯過時段的行情。",
                    "原預測圖保留分析日價格；盤中更新另發訊息。當日異常提醒最多一則。"])
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", str(repository)) and str(run_id).isdigit():
        message.append(f"https://github.com/{repository}/actions/runs/{run_id}")
    return "\n".join(message)


def send_health_alert(*, db, send_text, now=None, analysis_date="", repository="", run_id=""):
    if db is None:
        raise RuntimeError("無法確認告警寄送狀態；不繞過防重複檢查")
    current = now or datetime.now(TPE)
    if current.tzinfo is None:
        raise ValueError("告警時間必須包含時區")
    day = current.astimezone(TPE).date().isoformat()
    store = FirestoreReportStore(db, day, namespace="prediction_health", format_version=FORMAT)

    def build():
        text = build_health_message(now=current, analysis_date=analysis_date,
                                    repository=repository, run_id=run_id)
        return [text], {"checked_at": current.isoformat(), "run_id": str(run_id)}

    return deliver_report(store, [], day, build_messages=build, send_text=send_text,
                          fingerprint=f"{FORMAT}|{day}")


def main():
    try:
        scanner = import_module("scanner")
        # Reading the manifest is optional; an unavailable manifest must not
        # hide the alert when the notification ledger itself still works.
        try:
            manifest = scanner._load_daily_scan_doc() or {}
        except Exception:
            manifest = {}
        token, chat_id = scanner._telegram_credentials()
        sent = send_health_alert(
            db=scanner.db, send_text=lambda text: telegram_send_text(token, chat_id, text),
            analysis_date=manifest.get("scan_date", ""),
            repository=os.getenv("GITHUB_REPOSITORY", ""), run_id=os.getenv("GITHUB_RUN_ID", ""),
        )
        print("行情異常提醒已送出" if sent else "當日異常提醒已送出或寄送中，不重複通知")
    except Exception as exc:
        logging.error("行情異常提醒未確認（%s）；請查看失敗工作流程，不自動強制重送", type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
