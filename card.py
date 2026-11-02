"""Build a minimal, masked view before passing data to AstrBot's HTML renderer."""

import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CARD_TEMPLATE = Path(__file__).with_name("card.html").read_text(encoding="utf-8")
EMAIL = re.compile(r"[\w.!#$%&'*+/=?^`{|}~\-]+@[\w.-]+\.[a-z]{2,}", re.IGNORECASE)
LIVE_DATE = re.compile(r"(?<!\d)(20\d{2})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})日?")
DAY = re.compile(r"(?<![a-z0-9])(?:day\s*|d)(\d{1,2})(?!\d)", re.IGNORECASE)
START_TIME = re.compile(
    r"(?:START|開演|开演)\s*[:：]?\s*(\d{1,2}):([0-5]\d)", re.IGNORECASE
)
SEAT = re.compile(
    r"(?:[^\s/|·:：]*席[^\s/|·:：]*|[^\s/|·:：]*スタンディング[^\s/|·:：]*|"
    r"[^\s/|·:：]*整理券|[^\s/|·:：]*入場券|[^\s/|·:：]*指定エリア|立見(?:指定)?)"
    r"(?:\s*\d+\s*(?:枚|张))?"
)
JST = timezone(timedelta(hours=9))


def _mask_middle(value):
    if len(value) >= 6:
        return value[:2] + "***" + value[-2:]
    if len(value) >= 4:
        return value[:1] + "***" + value[-1:]
    return "***"


def mask_account(value):
    value = str(value or "").strip()
    if not value:
        return "未提供账号"
    if "@" in value:
        local, domain = value.rsplit("@", 1)
        return f"{_mask_middle(local)}@{domain}"
    return _mask_middle(value)


def _records(event):
    results = event.get("results")
    if not isinstance(results, list) or not results:
        results = event.get("entries", [])
    return (
        [row for row in results if isinstance(row, dict)]
        if isinstance(results, list)
        else []
    )


def _redactor(event, records):
    accounts = {str(event.get("account") or "")}
    accounts.update(str(row.get("account") or "") for row in records)
    accounts.discard("")

    def redact(value, limit=None):
        text = str(value or "")
        for account in sorted(accounts, key=len, reverse=True):
            text = text.replace(account, mask_account(account))
        text = EMAIL.sub(lambda match: mask_account(match.group()), text)
        return text if limit is None or len(text) <= limit else text[:limit] + "…"

    return redact


def _status(row):
    status = str(row.get("status") or row.get("category") or "").lower()
    if any(word in status for word in ("期限切", "expired", "已截止")):
        return "已过入金期限", "other", 4
    if any(word in status for word in ("落選", "落选", "未中选", "lost")):
        return "落选", "lost", 5
    if any(
        word in status
        for word in (
            "未入金",
            "待入金",
            "待付款",
            "支払待ち",
            "payment_required",
            "won_not_paid",
            "won_unpaid",
        )
    ):
        return "待入金", "unpaid", 1
    if any(
        word in status
        for word in ("入金済", "入金完了", "已入金", "已支付", "won_paid")
    ):
        return "已入金", "paid", 0
    if any(
        word in status
        for word in ("抽選待ち", "抽選中", "待开奖", "pending", "before_lottery")
    ):
        return "待开奖", "pending", 3
    if any(word in status for word in ("当選", "中选", "won")):
        return "中选", "won", 2
    return "其他状态", "other", 4


def _detail(row):
    return unicodedata.normalize(
        "NFKC", str(row.get("display_detail") or row.get("detail") or "")
    )


def _start_times(row):
    return sorted(
        {
            f"{int(hour):02d}:{minute}"
            for hour, minute in START_TIME.findall(_detail(row))
            if int(hour) < 24
        }
    )


def _seat_display(row):
    detail = _detail(row)
    seats = []
    for match in SEAT.finditer(detail):
        seat = match.group().strip()
        if seat not in {"座席", "席種", "席位"} and seat not in seats:
            seats.append(seat)
    if not seats:
        return "席位未提供"
    return ("志愿席位：" if len(seats) > 1 else "") + " / ".join(seats)


def _dates(row):
    dates = set()
    for match in LIVE_DATE.finditer(_detail(row)):
        try:
            dates.add(date(*map(int, match.groups())).isoformat())
        except ValueError:
            continue
    return sorted(dates)


def _day_info(row):
    detail = _detail(row)
    dates = _dates(row)
    # A multi-choice application must not be presented as a win on every day.
    if len(dates) > 1:
        return "multi", "", 0
    if len(_start_times(row)) > 1:
        return "multi_time", next(iter(dates), ""), 0
    text = detail + " " + unicodedata.normalize("NFKC", str(row.get("title") or ""))
    markers = {int(match.group(1)) for match in DAY.finditer(text)} - {0}
    explicit = next(iter(markers)) if len(markers) == 1 else 0
    if dates:
        return "date", next(iter(dates)), explicit
    if explicit:
        return "day", "", explicit
    return "unknown", "", 0


def _display_time(value):
    value = str(value or "")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is not None:
            return stamp.astimezone(JST).strftime("%Y/%m/%d %H:%M JST")
    except ValueError:
        pass
    return value


def build_card(event):
    records = _records(event)
    if not records or event.get("type") == "error":
        return None
    redact = _redactor(event, records)
    counts = {"paid": 0, "unpaid": 0, "won": 0, "pending": 0, "lost": 0, "other": 0}
    batches = {}
    has_cached = False
    batch = event.get("batch") if isinstance(event.get("batch"), dict) else {}
    for row in records:
        title = str(batch.get("title") or row.get("title") or "Live 抽选结果")
        draw = _display_time(
            batch.get("lottery_at_iso")
            or batch.get("lottery_at")
            or row.get("lottery_at_iso")
            or row.get("lottery_at")
            or ""
        )
        group = batches.setdefault((title, draw), {})
        kind, day_date, explicit = _day_info(row)
        starts = _start_times(row)
        start = (
            starts[0]
            if len(starts) == 1 and kind not in {"multi", "multi_time"}
            else ""
        )
        key = (kind, day_date, explicit if kind == "day" else 0, start)
        day = group.setdefault(
            key, {"date": day_date, "explicit": explicit, "rows": []}
        )
        label, tone, rank = _status(row)
        counts[tone] += 1
        cached = row.get("cached") is True
        has_cached = has_cached or cached
        day["rows"].append(
            {
                "account": mask_account(row.get("account") or event.get("account")),
                "label": label,
                "tone": tone,
                "rank": rank,
                "seat": redact(_seat_display(row)),
                "sessions": (
                    " / ".join(value.replace("-", "/") for value in _dates(row))
                    + (" · START " + " / ".join(starts) + " JST" if starts else "")
                    if kind in {"multi", "multi_time"}
                    else ""
                ),
                "detail": redact(_detail(row), 400),
                "status": redact(row.get("status"), 160),
                "cached": cached,
            }
        )
    lives = []
    for (title, draw), groups in batches.items():
        dated = sorted({group["date"] for group in groups.values() if group["date"]})
        # Explicit DAY labels anchor numbering when only part of a Live is known.
        offsets = {
            group["explicit"] - dated.index(group["date"])
            for group in groups.values()
            if group["date"] and group["explicit"]
        }
        offset = next(iter(offsets)) if len(offsets) == 1 else 1
        days = []
        for (kind, day_date, _, start), group in groups.items():
            if kind == "date":
                number = group["explicit"] or max(1, dated.index(day_date) + offset)
                label = f"D{number}"
                date_label = day_date.replace("-", "/")
                sort = (0, day_date, start or "99:99", number)
            elif kind == "day":
                label, date_label = f"D{group['explicit']}", "演出日期未提供"
                sort = (1, "", start or "99:99", group["explicit"])
            else:
                label = {"multi": "多日申请", "multi_time": "多场申请"}.get(
                    kind, "日期待确认"
                )
                date_label = (
                    "按原始申请展示"
                    if kind in {"multi", "multi_time"}
                    else "未从结果中识别到演出日期"
                )
                sort = (2, day_date, "", 0)
            rows = sorted(group["rows"], key=lambda row: (row["rank"], row["account"]))
            days.append(
                {
                    "label": label,
                    "date": date_label,
                    "time": f"START {start} JST" if start else "",
                    "rows": rows,
                    "sort": sort,
                }
            )
        days.sort(key=lambda day: day.pop("sort"))
        lives.append(
            {
                "title": redact(title, 180),
                "draw": redact(_display_time(draw), 100),
                "days": days,
            }
        )
    return {
        "test": event.get("type") == "test",
        "live_query": event.get("type") == "test"
        and event.get("test_source") == "live",
        "lives": lives,
        "counts": counts,
        "total": sum(counts.values()),
        "has_cached": has_cached,
        "created_at": redact(_display_time(event.get("created_at")), 100),
    }


def fallback_text(event):
    card = build_card(event)
    if card is None:
        redact = _redactor(event, _records(event))
        return redact(
            f"{event.get('title', 'ASOBI 通知')}\n\n{event.get('message', '')}"
        )
    lines = (
        ["【主动查询 · 实时结果】"]
        if card["live_query"]
        else (["【测试样例 · 非真实抽选结果】"] if card["test"] else [])
    )
    for live in card["lives"]:
        lines.append(f"LIVE · {live['title']}")
        if live["draw"]:
            lines.append(f"开奖批次：{live['draw']}")
        for day in live["days"]:
            lines.append(f"\n{day['label']} · {day['date']} {day['time']}".rstrip())
            for row in day["rows"]:
                suffix = "（最近缓存）" if row["cached"] else ""
                lines.append(
                    f"{row['seat']} · {row['account']}  [{row['label']}] {suffix}".rstrip()
                )
                if row["sessions"]:
                    lines.append(row["sessions"])
    return "\n".join(lines)
