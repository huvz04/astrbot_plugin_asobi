"""Limit queued result notifications to announcements already due today in Japan."""

import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def _lottery_time(row):
    for value in (row.get("lottery_at_iso"), row.get("lottery_at")):
        if not isinstance(value, str) or not value.strip():
            continue
        value = unicodedata.normalize("NFKC", value).strip()
        if not re.search(r"[T\s]\d{1,2}:\d{2}", value):
            continue
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            match = re.fullmatch(
                r"(\d{4})[年/](\d{1,2})[月/](\d{1,2})日?\s+(\d{1,2}):(\d{2})",
                value,
            )
            if not match:
                continue
            try:
                stamp = datetime(*map(int, match.groups()))
            except ValueError:
                continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=JST)
        try:
            return stamp.astimezone(JST)
        except (OverflowError, ValueError):
            continue
    return None


def filter_today_event(event, *, now=None):
    """Return a copy containing only due rows, or None when there is no result to send.

    ``now`` is a Unix timestamp or datetime; a naive datetime means Japan time.
    Delivery time, not the event creation time, decides which day is eligible.
    """
    if event.get("type") == "error":
        return event
    if isinstance(now, datetime):
        current = now.replace(tzinfo=JST) if now.tzinfo is None else now.astimezone(JST)
    else:
        current = datetime.fromtimestamp(time.time() if now is None else now, JST)

    kept = {}
    for field in ("results", "entries"):
        rows = event.get(field)
        kept[field] = []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            draw = _lottery_time(row)
            if draw is not None and draw.date() == current.date() and draw <= current:
                kept[field].append(dict(row))

    if not any(kept.values()):
        # Retain simple connectivity tests, but never turn an empty result into
        # a fallback using its unfiltered historical message.
        plain_test = (
            event.get("type") == "test"
            and event.get("test_source") != "live"
            and not event.get("batch")
            and all(event.get(field) in (None, []) for field in ("results", "entries"))
        )
        return event if plain_test else None

    scoped = dict(event)
    scoped.update(kept)
    scoped.pop("batch", None)
    scoped["title"] = "ASOBI 今日抽选结果"
    scoped["message"] = "仅包含日本时间当天已到开奖时间的申请。"
    records = kept["results"] or kept["entries"]
    accounts = {
        row["account"]
        for row in records
        if isinstance(row.get("account"), str) and row["account"].strip()
    }
    if accounts:
        scoped["account"] = ", ".join(sorted(accounts))
    return scoped
