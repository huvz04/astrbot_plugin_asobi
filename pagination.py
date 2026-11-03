"""Paginate masked card views without dropping or duplicating applications."""

import math
import unicodedata

MAX_ROWS = 12
MAX_GROUPS = 4
MAX_ESTIMATED_HEIGHT = 1800
MAX_PAGES = 12


class CardTooLargeError(ValueError):
    """Use the full text notification when bounded image rendering is impractical."""


def _lines(value, width):
    lines = 0
    for line in str(value or "").splitlines():
        units = sum(
            0
            if unicodedata.combining(char)
            else 2
            if unicodedata.east_asian_width(char) in {"W", "F"}
            else 1
            for char in line
        )
        lines += max(1, math.ceil(units / width))
    return lines


def _row_height(row):
    # Conservative wrapping estimates for the 840 px template. Count fields
    # separately because flex wrapping can put the account on another line.
    return max(
        90,
        36
        + _lines(row.get("seat"), 50) * 28
        + _lines(row.get("account"), 50) * 25
        + _lines(row.get("sessions"), 70) * 22,
    )


def paginate_card(view):
    pages = []

    def finish_page(live, days):
        pages.append({**view, "lives": [{**live, "days": days}]})
        if len(pages) > MAX_PAGES:
            raise CardTooLargeError

    for live in view["lives"]:
        base_height = (
            140 + _lines(live.get("title"), 60) * 38 + _lines(live.get("draw"), 85) * 24
        )
        days, row_count, height, last_day = [], 0, base_height, None

        for day in live["days"]:
            for row in day["rows"]:
                row_height = _row_height(row)
                new_group = last_day is not day
                if row_count and (
                    row_count >= MAX_ROWS
                    or (new_group and len(days) >= MAX_GROUPS)
                    or height + row_height + (100 if new_group else 0)
                    > MAX_ESTIMATED_HEIGHT
                ):
                    finish_page(live, days)
                    days, row_count, height, last_day = [], 0, base_height, None
                    new_group = True
                if (
                    height + row_height + (100 if new_group else 0)
                    > MAX_ESTIMATED_HEIGHT
                ):
                    raise CardTooLargeError
                if new_group:
                    days.append({**day, "rows": []})
                    height += 100
                    last_day = day
                days[-1]["rows"].append(row)
                row_count += 1
                height += row_height
        if row_count:
            finish_page(live, days)
    if not pages:
        raise ValueError("Card has no application rows")
    return [
        {**page, "page_number": number, "page_count": len(pages)}
        for number, page in enumerate(pages, 1)
    ]
