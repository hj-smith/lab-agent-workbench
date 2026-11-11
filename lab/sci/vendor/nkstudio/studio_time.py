"""Beijing time for display and for new records (platform rule common-rules §5f).

Stored values keep their own time zone and are never rewritten. Text without a
time zone (instrument files, dates in file names) is shown as it is.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# China has no daylight saving time, so a fixed +08:00 needs no tz database.
BEIJING = timezone(timedelta(hours=8), '北京时间')
TIME_KEYS = {'timestamp', 'time', 'at', 'imported_at', 'updated_at', 'created_at',
             'deleted_at', 'modified_at', 'started_at', 'import_time', 'export_time'}


def now() -> datetime:
    return datetime.now(BEIJING)


def now_iso(timespec: str = 'seconds') -> str:
    return now().isoformat(timespec=timespec)


def stamp(fmt: str) -> str:
    """Beijing-time text for file and folder names."""
    return now().strftime(fmt)


def show(value, date_only: bool = False):
    """'2026-10-22 16:21' in Beijing time for zone-aware ISO text; anything else unchanged."""
    if not isinstance(value, str) or len(value) < 16:
        return value
    try:
        moment = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return value
    if moment.tzinfo is None:
        return value
    return moment.astimezone(BEIJING).strftime('%Y-%m-%d' if date_only else '%Y-%m-%d %H:%M')
