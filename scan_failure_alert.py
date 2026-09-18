"""Send a minimal Telegram alert when the authoritative daily scan fails.

The alert deliberately excludes exception text and provider payloads so secrets
cannot be copied from a failed GitHub Actions log into Telegram.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TPE = timezone(timedelta(hours=8))


def build_failure_message(*, repository: str = "", run_id: str = "", now: datetime | None = None) -> str:
    current = (now or datetime.now(TPE)).astimezone(TPE)
    message = [
        "⚠️ 台股每日掃描失敗",
        current.strftime("台北時間 %Y-%m-%d %H:%M"),
        "本次流程未完整完成；榜單可能保留舊資料或已部分更新，請核對首頁資料日期與 GitHub Actions。",
    ]
    if repository and run_id:
        message.append(f"https://github.com/{repository}/actions/runs/{run_id}")
    return "\n".join(message)


def send_failure_alert(
    token: str,
    chat_id: str,
    *,
    repository: str = "",
    run_id: str = "",
    session: Any | None = None,
) -> None:
    token = str(token or "").strip()
    chat_id = str(chat_id or "").strip()
    if not token or not chat_id:
        raise RuntimeError("Telegram failure-alert credentials are missing")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": build_failure_message(repository=repository, run_id=run_id),
        "disable_web_page_preview": "true",
    }
    if session is not None:
        response = session.post(url, data=payload, timeout=15)
        response.raise_for_status()
        return

    # Keep the workflow failure path independent of third-party packages: an
    # install failure should not prevent this alert from reaching Telegram.
    request = Request(
        url,
        data=urlencode(payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        response.read(1)


def main() -> None:
    send_failure_alert(
        os.getenv("TELEGRAM_BOT_TOKEN", ""),
        os.getenv("TELEGRAM_CHAT_ID", ""),
        repository=os.getenv("GITHUB_REPOSITORY", ""),
        run_id=os.getenv("GITHUB_RUN_ID", ""),
    )


if __name__ == "__main__":
    main()
