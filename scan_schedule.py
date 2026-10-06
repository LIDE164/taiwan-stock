"""Verified post-close scan windows, including bounded pre-open recovery.

Recovery uses the preceding completed session's actual observations, never an
intraday candle or a fabricated prior-night publication timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from market_calendar import is_scheduled_session

TPE = timezone(timedelta(hours=8))


def _local(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("掃描時鐘必須包含時區")
    return now.astimezone(TPE)


@dataclass(frozen=True)
class ScanWindow:
    analysis_date: date
    started_at: datetime
    publish_before: datetime


def scan_window(now: datetime) -> ScanWindow | None:
    """Allow post-close today or a previous-session recovery started before 08:30.

    The calendar must cover every inspected date. Recovery always expires at
    09:00 of its start day; even a post-close job crossing midnight must finish
    before 09:00 the next day. Closed-session daytime calls are safe no-ops.
    """
    local = _local(now)
    day = local.date()
    scheduled = is_scheduled_session(day)
    if scheduled is None:
        raise ValueError("缺少已驗證交易日曆，無法確認盤後掃描日期")
    if local.time() < time(8, 30):
        previous = day - timedelta(days=1)
        while True:
            status = is_scheduled_session(previous)
            if status is None:
                raise ValueError("缺少已驗證前一交易日，禁止猜測補掃日期")
            if status:
                return ScanWindow(previous, local, datetime.combine(day, time(9), TPE))
            previous -= timedelta(days=1)
    if scheduled and local.time() >= time(14, 30):
        deadline = datetime.combine(day + timedelta(days=1), time(9), TPE)
        return ScanWindow(day, local, deadline)
    return None


def ensure_publish_allowed(window: ScanWindow, now: datetime) -> datetime:
    """Recheck the wall clock immediately before publishing durable results."""
    local = _local(now)
    if local < window.started_at:
        raise RuntimeError("掃描時鐘倒退，禁止發布無法驗證時點的榜單")
    if local >= window.publish_before:
        raise RuntimeError("已超過盤前補掃截止時間 09:00，禁止事後寫入前日榜單或績效")
    return local


def execution_metadata(window: ScanWindow, generated_at: datetime) -> dict[str, str | bool]:
    local = ensure_publish_allowed(window, generated_at)
    delayed = local.date() > window.analysis_date
    return {
        "generated_at": local.isoformat(),
        "scan_started_at": window.started_at.isoformat(),
        "data_as_of_date": window.analysis_date.isoformat(),
        "scan_mode": "delayed_preopen_recovery" if delayed else "postclose",
        "delayed_recovery": delayed,
        "availability_note": "延遲盤前補掃；生成時間不代表分析日當晚已存在" if delayed else "盤後當日生成",
    }


def delayed_publication_note(records: Sequence[Mapping[str, Any]], analysis_date: str, *, now: datetime) -> str:
    """Describe a delayed publication only from consistent saved row evidence."""
    if not any(isinstance(row, Mapping) and row.get("Scan_Delayed_Recovery") is True for row in records):
        return ""
    generated: datetime | None = None
    for row in records:
        if (not isinstance(row, Mapping) or row.get("Scan_Delayed_Recovery") is not True
                or row.get("Data_Date") != analysis_date):
            raise ValueError("延遲補掃的日期或來源標記不一致")
        stamp = row.get("Scan_Generated_At")
        try:
            observed = _local(datetime.fromisoformat(stamp)) if isinstance(stamp, str) else None
        except ValueError:
            observed = None
        if observed is None or (generated is not None and observed != generated):
            raise ValueError("延遲補掃缺少一致且含時區的真實生成時間")
        generated = observed
    if generated is None or generated > _local(now) or generated.time() >= time(9):
        raise ValueError("延遲補掃生成時間不在已發生的盤前時段")
    expected = scan_window(generated.replace(hour=0, minute=0, second=0, microsecond=0))
    if expected is None or expected.analysis_date.isoformat() != analysis_date:
        raise ValueError("延遲補掃生成時間與分析日不符")
    return (f"延遲盤前補掃｜分析日 {analysis_date}｜實際生成 {generated:%Y-%m-%d %H:%M:%S}（台北）；"
            "非分析日當晚已發布的名單。")
