"""
기간 표현 → 실제 날짜 범위 (코드가 계산)

LLM 은 '오늘이 며칠인지' 를 정확히 모르고 '지난주' 같은 계산을 가끔 틀린다.
그래서 LLM/규칙 플래너는 상대 표현만 만들고, 날짜 계산은 여기서 한다.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from app.services.chat.schemas import Period, PeriodKind, PeriodUnit
from app.core.config import settings


@dataclass
class DateRange:
    start: date   # 포함
    end: date     # 미포함
    label: str


def today() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    return date(d.year + m // 12, m % 12 + 1, 1)


def _fmt(d: date) -> str:
    return f"{d.month}/{d.day}"


_RELATIVE_NAMES = {
    PeriodUnit.DAY: {0: "오늘", -1: "어제", -2: "그제"},
    PeriodUnit.WEEK: {0: "이번 주", -1: "지난주"},
    PeriodUnit.MONTH: {0: "이번 달", -1: "지난달"},
    PeriodUnit.YEAR: {0: "올해", -1: "작년"},
}


def resolve(period: Period, base: Optional[date] = None) -> Optional[DateRange]:
    """None = 기간 제한 없음"""
    t = base or today()
    kind = period.kind

    if kind == PeriodKind.RELATIVE and period.unit:
        off = period.offset or 0
        name = _RELATIVE_NAMES.get(period.unit, {}).get(off)
        if period.unit == PeriodUnit.DAY:
            s = t + timedelta(days=off)
            return DateRange(s, s + timedelta(days=1), f"{name or f'{-off}일 전'}({_fmt(s)})")
        if period.unit == PeriodUnit.WEEK:
            s = t - timedelta(days=t.weekday()) + timedelta(weeks=off)
            e = s + timedelta(days=7)
            return DateRange(s, e, f"{name or f'{-off}주 전'}({_fmt(s)}~{_fmt(e - timedelta(days=1))})")
        if period.unit == PeriodUnit.MONTH:
            s = _add_months(t.replace(day=1), off)
            return DateRange(s, _add_months(s, 1), f"{name or f'{-off}달 전'}({s.year}년 {s.month}월)")
        if period.unit == PeriodUnit.YEAR:
            y = t.year + off
            return DateRange(date(y, 1, 1), date(y + 1, 1, 1), f"{name or f'{-off}년 전'}({y}년)")

    if kind == PeriodKind.LAST_N_DAYS and period.days:
        n = max(1, min(period.days, 3650))
        s = t - timedelta(days=n - 1)
        return DateRange(s, t + timedelta(days=1), f"최근 {n}일({_fmt(s)}~{_fmt(t)})")

    if kind == PeriodKind.MONTH_OF_YEAR and period.month and 1 <= period.month <= 12:
        y = period.year
        if y is None:
            # 연도를 말하지 않았으면 가장 최근의 그 달 (지금이 11월인데 '12월' 이면 작년 12월)
            y = t.year if period.month <= t.month else t.year - 1
        s = date(y, period.month, 1)
        return DateRange(s, _add_months(s, 1), f"{y}년 {period.month}월")

    if kind == PeriodKind.ABSOLUTE and period.startDate:
        try:
            s = date.fromisoformat(period.startDate)
            e = date.fromisoformat(period.endDate) if period.endDate else s
        except ValueError:
            return None
        if e < s:
            s, e = e, s
        label = f"{s.isoformat()}" if s == e else f"{s.isoformat()}~{e.isoformat()}"
        return DateRange(s, e + timedelta(days=1), label)

    return None
