"""手動交易日誌的驗證與覆盤；不讀取或推算任何模擬交易。

金額單位為新臺幣。實際費稅由使用者填寫，不以估算值代替；缺漏值
保留為 ``None``。本模組只整理紀錄與可核對的事實，不提供個股指令。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Any

from execution_costs import estimate_stop_loss


_FIELDS = {
    "id", "status", "ticker", "name", "entry_date", "entry_price", "shares",
    "exit_date", "exit_price", "actual_costs", "planned_entry_low",
    "planned_entry_high", "planned_stop", "planned_target", "entry_reason",
    "exit_reason", "emotion", "missed_reason", "observed_date",
    "observed_price", "decision_date", "notes",
}
_TEXT_FIELDS = ("id", "name", "entry_reason", "exit_reason", "missed_reason", "notes")
_PRICE_FIELDS = (
    "entry_price", "exit_price", "planned_entry_low", "planned_entry_high",
    "planned_stop", "planned_target", "observed_price",
)
_DATES = ("entry_date", "exit_date", "decision_date", "observed_date")
_TICKER = re.compile(r"[0-9]{4,6}[A-Z]?(?:\.(?:TW|TWO))?\Z")
_RECORD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_EMOTION_TAGS = {
    "FOMO", "害怕錯過", "追高焦慮", "焦慮", "恐懼", "貪婪", "衝動",
    "急躁", "報復交易", "不甘心", "僥倖", "過度自信",
    "怕錯過", "急於回本", "不願認賠", "怕獲利回吐", "臨時改單",
}
_RISK_LIMIT = 5000.0
# Keep every intermediate product and percentage finite, including imported JSON.
# These bounds are far outside plausible Taiwan-equity trade quantities.
_MAX_PRICE_OR_COST = 1_000_000_000_000.0
_MIN_POSITIVE_PRICE = 0.00000001
_MAX_SHARES = 1_000_000_000_000


def _text(value: Any, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} 必須是文字")
    result = value.strip()
    if len(result) > 2000:
        raise ValueError(f"{field} 文字過長")
    return result or None


def _number(value: Any, field: str, *, allow_zero: bool = False) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{field} 必須是數字")
    try:
        result = float(value)
    except (ValueError, OverflowError, InvalidOperation) as exc:
        raise ValueError(f"{field} 必須是有限數字") from exc
    if not math.isfinite(result) or result < 0 or (not allow_zero and result == 0):
        raise ValueError(f"{field} 必須是{'非負' if allow_zero else '正'}的有限數字")
    if result > _MAX_PRICE_OR_COST or (not allow_zero and result < _MIN_POSITIVE_PRICE):
        raise ValueError(f"{field} 超出可安全計算的範圍")
    return result


def _date(value: Any, field: str) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        raise ValueError(f"{field} 必須是日期，不可含時間")
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise ValueError(f"{field} 必須是 YYYY-MM-DD 日期")
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{field} 必須是有效的 YYYY-MM-DD 日期") from exc
    if parsed.isoformat() != value.strip():
        raise ValueError(f"{field} 必須是 YYYY-MM-DD 日期")
    return parsed.isoformat()


def _emotion(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        values = re.split(r"[,，、;；]", value)
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        raise ValueError("emotion 必須是文字或文字清單")
    tags: list[str] = []
    for item in values:
        tag = _text(item, "emotion")
        if tag and tag not in tags:
            tags.append(tag)
    if len(tags) > 10:
        raise ValueError("emotion 標籤過多")
    return tags


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    """驗證一筆手動紀錄並回傳可供 JSON 儲存的標準欄位。

    ``closed`` 必須有實際進出場，``open`` 只有實際進場，``missed``
    只有手動決策及觀察資料。缺費稅不會被補成零。無效資料一律拋出 ValueError。
    """
    if not isinstance(record, dict):
        raise ValueError("交易紀錄必須是 dict")
    unknown = set(record) - _FIELDS
    if unknown:
        raise ValueError(f"未知欄位：{', '.join(sorted(unknown))}")
    result: dict[str, Any] = {field: None for field in _FIELDS}
    for field in _TEXT_FIELDS:
        result[field] = _text(record.get(field), field)
    for field in _PRICE_FIELDS:
        result[field] = _number(record.get(field), field)
    result["actual_costs"] = _number(record.get("actual_costs"), "actual_costs", allow_zero=True)
    for field in _DATES:
        result[field] = _date(record.get(field), field)
    result["emotion"] = _emotion(record.get("emotion"))

    if not result["id"] or not _RECORD_ID.fullmatch(result["id"]):
        raise ValueError("id 必須是 1–64 字元的英數、底線或連字號識別碼")

    ticker = _text(record.get("ticker"), "ticker")
    if not ticker or not _TICKER.fullmatch(ticker.upper()):
        raise ValueError("ticker 必須是有效的臺股代號")
    result["ticker"] = ticker.upper()
    status = record.get("status")
    if not isinstance(status, str) or status not in {"closed", "open", "missed"}:
        raise ValueError("status 必須是 closed、open 或 missed")
    result["status"] = status

    shares = record.get("shares")
    if shares is not None and shares != "":
        if isinstance(shares, bool) or not isinstance(shares, int) or not 0 < shares <= _MAX_SHARES:
            raise ValueError("shares 必須是 1 至 1 兆的整數")
        result["shares"] = shares
    low, high = result["planned_entry_low"], result["planned_entry_high"]
    if low is not None and high is not None and low > high:
        raise ValueError("planned_entry_low 不可高於 planned_entry_high")
    stop, target = result["planned_stop"], result["planned_target"]
    if stop is not None and target is not None and stop >= target:
        raise ValueError("planned_stop 必須低於 planned_target")

    if status in {"closed", "open"}:
        if any(result[field] is None for field in ("entry_date", "entry_price", "shares")):
            raise ValueError("實際交易必須填入 entry_date、entry_price、shares")
        if result["decision_date"] is not None and result["decision_date"] > result["entry_date"]:
            raise ValueError("decision_date 不可晚於 entry_date")
        if stop is not None and stop >= result["entry_price"]:
            raise ValueError("planned_stop 必須低於實際進場價")
        if any(result[field] is not None for field in ("observed_date", "observed_price", "missed_reason")):
            raise ValueError("實際交易不可混入錯過機會欄位")
        if status == "closed":
            if result["exit_date"] is None or result["exit_price"] is None:
                raise ValueError("closed 必須有實際 exit_date 與 exit_price")
            if result["exit_date"] < result["entry_date"]:
                raise ValueError("exit_date 不可早於 entry_date")
        elif any(result[field] is not None for field in ("exit_date", "exit_price", "exit_reason")):
            raise ValueError("open 不可有實際出場欄位")
    else:
        if any(result[field] is None for field in ("decision_date", "observed_date", "observed_price")):
            raise ValueError("missed 必須有手動 decision_date、observed_date 與 observed_price")
        if result["observed_date"] < result["decision_date"]:
            raise ValueError("observed_date 不可早於 decision_date")
        if any(result[field] is not None for field in (
            "entry_date", "entry_price", "shares", "exit_date", "exit_price",
            "actual_costs", "exit_reason",
        )):
            raise ValueError("missed 不可混入實際成交或費稅")
    return result


def _money(value: float) -> float:
    return round(value, 2)


def derive_record_metrics(record: dict[str, Any]) -> dict[str, Any]:
    """逐筆計算可觀察指標；沒有實際出場時所有損益欄位為 None。"""
    item = normalize_record(record)
    status = item["status"]
    metrics: dict[str, Any] = {
        "gross_pnl": None, "net_pnl": None, "net_return_pct": None,
        "planned_risk": None, "observed_price_gap": None,
        "observed_price_gap_pct": None,
        "entry_above_plan": False, "stop_execution_deviation": False,
        "excessive_planned_risk": False, "early_profitable_exit_review": False,
        "possible_emotion_bias": False, "emotion_evidence_tags": [],
        "missed_opportunity": status == "missed",
    }
    tags = [tag for tag in item["emotion"] if tag.upper() in _EMOTION_TAGS or tag in _EMOTION_TAGS]
    metrics["possible_emotion_bias"] = bool(tags)
    metrics["emotion_evidence_tags"] = tags
    if status == "missed":
        reference = item["planned_entry_high"]
        if reference is not None:
            gap = item["observed_price"] - reference
            metrics["observed_price_gap"] = _money(gap)
            metrics["observed_price_gap_pct"] = round(gap / reference * 100, 2)
        return metrics

    entry, shares = item["entry_price"], item["shares"]
    high = item["planned_entry_high"]
    stop = item["planned_stop"]
    metrics["entry_above_plan"] = high is not None and entry > high
    if stop is not None and stop < entry:
        # Include modeled commissions, tax and stop slippage, matching the
        # existing per-trade 5,000 NT$ risk budget.  Gaps can exceed this.
        estimate = estimate_stop_loss(entry, stop, shares)
        metrics["planned_risk"] = (
            _money(estimate.estimated_net_loss) if estimate is not None else None
        )
        metrics["excessive_planned_risk"] = (
            metrics["planned_risk"] is not None
            and metrics["planned_risk"] > _RISK_LIMIT
        )
    if status == "closed":
        exit_price = item["exit_price"]
        gross = _money((exit_price - entry) * shares)
        metrics["gross_pnl"] = gross
        if item["actual_costs"] is not None:
            net = _money(gross - item["actual_costs"])
            metrics["net_pnl"] = net
            metrics["net_return_pct"] = round(net / (entry * shares) * 100, 2)
        metrics["stop_execution_deviation"] = stop is not None and exit_price < stop
        target = item["planned_target"]
        metrics["early_profitable_exit_review"] = (
            target is not None and exit_price < target and metrics["net_pnl"] is not None
            and metrics["net_pnl"] > 0
        )
    return metrics


_FINDING_CONFIG = (
    ("missed_opportunity", "手動標記的錯過機會", "missed_opportunity"),
    ("entry_above_plan", "實際進場高於預定上限", "entry_above_plan"),
    ("stop_execution_deviation", "實際出場低於預定停損（待辨識原因）", "stop_execution_deviation"),
    ("excessive_planned_risk", "單筆估計停損風險超過 5,000 元", "excessive_planned_risk"),
    ("early_profitable_exit_review", "獲利但低於預定目標出場（待覆盤）", "early_profitable_exit_review"),
    ("possible_emotion_bias", "自填情緒標籤顯示可能的行為影響", "possible_emotion_bias"),
)
_RULES = {
    "missed_opportunity": ("覆盤反覆錯過的條件", "未進場時先記下當時未達的條件與截止時間；事後只檢查原條件是否合理，不把觀察漲幅當作漏賺。"),
    "entry_above_plan": ("先核對入場上限", "送單前比對原計畫上限；若價格已高於上限，先停止該筆下單並記錄是否重新制定計畫，不以盤後結果倒改門檻。"),
    "stop_execution_deviation": ("記錄停損偏離原因", "實際出場低於預定停損時，當日記下是否跳空、流動性或執行延遲；下次下單前先檢查同類風險。"),
    "excessive_planned_risk": ("下單前檢查 5,000 元風險", "用進場價、停損價與股數連同模型費稅試算；若估計損失超過單筆 5,000 元上限，先縮小股數或不建立該筆計畫。"),
    "early_profitable_exit_review": ("目標前出場要留理由", "若獲利但提前離開原目標，當下記錄新的風險事實與出場理由；覆盤時再判斷是否合理，不預設提前出場是錯誤。"),
    "possible_emotion_bias": ("情緒標籤觸發複核", "若自評怕錯過、急於回本或臨時改單，先暫停並重核對原計畫的進場、停損與股數；標籤只作提示，不當成心理診斷。"),
}


def analyze_journal(records: list[dict[str, Any]], recent_limit: int = 30) -> dict[str, Any]:
    """彙總全期紀錄，並對最近 ``recent_limit`` 筆提供證據與三條規則。

    最近筆數依手動事件日期排序；實際交易採進場日，錯過機會採決策日。
    ``rows`` 亦只包含此近期樣本，每列有 ``record`` 和 ``metrics``。
    """
    if not isinstance(records, list):
        raise ValueError("records 必須是紀錄清單")
    if isinstance(recent_limit, bool) or not isinstance(recent_limit, int) or recent_limit <= 0:
        raise ValueError("recent_limit 必須是正整數")
    normalized = [normalize_record(record) for record in records]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in normalized:
        evidence_id = record["id"]
        if evidence_id in seen:
            raise ValueError(f"紀錄 id 重複：{evidence_id}")
        seen.add(evidence_id)
        rows.append({
            "id": evidence_id, "record": record, "metrics": derive_record_metrics(record),
            "event_date": record["decision_date"] if record["status"] == "missed" else record["entry_date"],
        })
    rows.sort(key=lambda row: row["event_date"], reverse=True)
    recent = rows[:recent_limit]

    closed = [row for row in rows if row["record"]["status"] == "closed"]
    known_net = [row["metrics"]["net_pnl"] for row in closed if row["metrics"]["net_pnl"] is not None]
    summary = {
        "total_records": len(rows),
        "closed_count": len(closed),
        "open_count": sum(row["record"]["status"] == "open" for row in rows),
        "missed_count": sum(row["record"]["status"] == "missed" for row in rows),
        "reviewed_count": len(recent),
        "realized_gross_pnl": _money(sum(row["metrics"]["gross_pnl"] for row in closed)) if closed else None,
        "realized_net_pnl": _money(sum(known_net)) if closed and len(known_net) == len(closed) else None,
        "known_net_pnl": _money(sum(known_net)) if known_net else None,
        "known_net_count": len(known_net),
        "unknown_cost_count": len(closed) - len(known_net),
        "net_win_rate_pct": (
            round(sum(value > 0 for value in known_net) / len(known_net) * 100, 2)
            if known_net else None
        ),
    }

    def eligible(key: str, row: dict[str, Any]) -> bool:
        record, metrics = row["record"], row["metrics"]
        status = record["status"]
        if key == "missed_opportunity":
            return True
        if key == "entry_above_plan":
            return status != "missed" and record["planned_entry_high"] is not None
        if key == "stop_execution_deviation":
            return status == "closed" and record["planned_stop"] is not None
        if key == "excessive_planned_risk":
            return status != "missed" and metrics["planned_risk"] is not None
        if key == "early_profitable_exit_review":
            return status == "closed" and record["planned_target"] is not None and metrics["net_pnl"] is not None
        return bool(record["emotion"])

    findings: list[dict[str, Any]] = []
    for key, label, metric_key in _FINDING_CONFIG:
        candidates = [row for row in recent if eligible(key, row)]
        matches = [row for row in candidates if row["metrics"][metric_key]]
        evidence = []
        for row in matches:
            record, metrics = row["record"], row["metrics"]
            details: dict[str, Any] = {"id": row["id"], "ticker": record["ticker"], "event_date": row["event_date"]}
            if key == "missed_opportunity":
                details.update(
                    decision_date=record["decision_date"], observed_date=record["observed_date"],
                    missed_reason=record["missed_reason"], observed_price=record["observed_price"],
                    observed_price_gap=metrics["observed_price_gap"],
                )
            elif key == "entry_above_plan":
                details.update(actual_entry=record["entry_price"], planned_high=record["planned_entry_high"])
            elif key == "stop_execution_deviation":
                details.update(actual_exit=record["exit_price"], planned_stop=record["planned_stop"])
            elif key == "excessive_planned_risk":
                details.update(planned_risk=metrics["planned_risk"], threshold=_RISK_LIMIT)
            elif key == "early_profitable_exit_review":
                details.update(
                    actual_exit=record["exit_price"], planned_target=record["planned_target"],
                    net_pnl=metrics["net_pnl"],
                )
            else:
                details["self_reported_tags"] = metrics["emotion_evidence_tags"]
            evidence.append(details)
        count = len(matches)
        repeated_reason = None
        repeated_reason_count = 0
        repeated_reason_ids: list[str] = []
        if key == "missed_opportunity":
            reasons: dict[str, list[str]] = {}
            for row in matches:
                reason = row["record"]["missed_reason"]
                if reason:
                    reasons.setdefault(reason.casefold(), []).append(row["id"])
            if reasons:
                repeated_reason_key, repeated_reason_ids = sorted(
                    reasons.items(), key=lambda pair: (-len(pair[1]), pair[0])
                )[0]
                repeated_reason_count = len(repeated_reason_ids)
                if repeated_reason_count >= 2:
                    repeated_reason = next(
                        row["record"]["missed_reason"] for row in matches
                        if row["record"]["missed_reason"]
                        and row["record"]["missed_reason"].casefold() == repeated_reason_key
                    )
        if key == "missed_opportunity":
            interpretation = (
                "錯過機會皆為手動標記；相同自填理由重複出現，觀察價差不等於可實現損益。"
                if repeated_reason else
                "錯過機會皆為手動標記；尚無重複的自填理由，不推算未實現報酬。"
            )
        elif key == "early_profitable_exit_review":
            interpretation = "僅標示待覆盤；低於原目標出場不必然是錯誤。"
        elif key == "possible_emotion_bias":
            interpretation = "僅依自填情緒標籤提示可能影響，不從價格漲跌推斷動機。"
        elif count < 2:
            interpretation = "近期證據不足以稱為重複模式。"
        else:
            interpretation = "近期樣本中重複出現，請核對逐筆證據。"
        findings.append({
            "key": key, "label": label, "count": count, "denominator": len(candidates),
            "rate": round(count / len(candidates), 4) if candidates else None,
            "repeated": count >= 2, "evidence_ids": [row["id"] for row in matches],
            "evidence": evidence, "interpretation": interpretation,
            "repeated_reason": repeated_reason, "repeated_reason_count": repeated_reason_count,
            "repeated_reason_evidence_ids": repeated_reason_ids if repeated_reason else [],
        })

    supported = [
        finding for finding in findings
        if (
            finding["key"] == "missed_opportunity" and finding["repeated_reason_count"] >= 2
        ) or (
            finding["key"] != "missed_opportunity"
            and finding["count"] >= 2 and finding["denominator"] >= 3
        )
    ]
    supported.sort(key=lambda finding: (-finding["rate"], -finding["count"], finding["key"]))
    rules = []
    for finding in supported[:3]:
        title, rule_text = _RULES[finding["key"]]
        basis = f"近期 {finding['count']}/{finding['denominator']} 筆符合；證據：{', '.join(finding['evidence_ids'])}"
        if finding["key"] == "missed_opportunity":
            basis += (
                f"；相同自填理由「{finding['repeated_reason']}」"
                f" 出現 {finding['repeated_reason_count']} 次"
            )
        rules.append({
            "title": title, "text": rule_text,
            "basis": basis,
            "personalized": True, "finding_key": finding["key"],
        })
    for key in (
        "entry_above_plan", "excessive_planned_risk", "stop_execution_deviation",
        "missed_opportunity", "early_profitable_exit_review", "possible_emotion_bias",
    ):
        if len(rules) == 3:
            break
        if any(rule["finding_key"] == key for rule in rules):
            continue
        title, rule_text = _RULES[key]
        rules.append({
            "title": title, "text": rule_text, "basis": "基礎規則；近期可比較樣本不足或尚未形成重複模式。",
            "personalized": False, "finding_key": key,
        })
    return {"summary": summary, "findings": findings, "rules": rules, "rows": recent}
