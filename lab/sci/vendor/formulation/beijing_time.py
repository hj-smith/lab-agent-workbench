"""Beijing clock, typed input, and presentation without changing stored history."""
import datetime as dt


BEIJING = dt.timezone(dt.timedelta(hours=8), 'Asia/Shanghai')


def now(value=None):
    return dt.datetime.now(BEIJING) if value is None else parse_input(value)


def today(value=None):
    return now(value).date()


def parse_input(value):
    """Interpret a new naive input as Beijing time and preserve aware instants."""
    moment = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if moment.tzinfo is None or moment.utcoffset() is None:
        return moment.replace(tzinfo=BEIJING)
    return moment.astimezone(BEIJING)


def format_time(value):
    """Format aware instants in Beijing; leave old naive or invalid text intact."""
    if value is None or value == '':
        return '—'
    try:
        if isinstance(value, (int, float)):
            moment = dt.datetime.fromtimestamp(value, BEIJING)
        elif isinstance(value, dt.datetime):
            moment = value
        else:
            moment = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if moment.tzinfo is None or moment.utcoffset() is None:
            return str(value)
        return moment.astimezone(BEIJING).strftime('%Y-%m-%d %H:%M')
    except (ValueError, TypeError, OverflowError, OSError):
        return str(value)


def format_date(value):
    """Format aware instants as Beijing dates; preserve naive dates and text."""
    if value is None or value == '':
        return '—'
    try:
        if isinstance(value, (int, float)):
            moment = dt.datetime.fromtimestamp(value, BEIJING)
        elif isinstance(value, dt.datetime):
            moment = value
        else:
            moment = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if moment.tzinfo is None or moment.utcoffset() is None:
            return str(value)
        return moment.astimezone(BEIJING).strftime('%Y-%m-%d')
    except (ValueError, TypeError, OverflowError, OSError):
        return str(value)
