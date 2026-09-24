
"""带偏移量的 ISO 8601 时间解析与比较。"""

from datetime import datetime, timezone


def parse(value: str, field: str = "时间") -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field}必须是 ISO 8601 字符串")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field}不是合法的 ISO 8601 时间：{value}") from exc
    if dt.tzinfo is None:
        raise ValueError(f"{field}必须带时区偏移量：{value}")
    return dt


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_order(start: str, end: str | None, end_field: str = "结束时间") -> None:
    if end is not None and parse(end, end_field) < parse(start, "开始时间"):
        raise ValueError(f"{end_field}不能早于开始时间")
