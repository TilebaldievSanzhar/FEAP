"""Время Кыргызстана для дат курсов, ставок и проектов ДТ.

Сервер в Docker живёт в UTC: dt.date.today() с 00:00 до 06:00 по Бишкеку даёт
вчерашнюю дату, и расчёт берёт вчерашний курс. Все «сегодня» в деньгах — отсюда.
Бишкек — UTC+6 без перехода на летнее время (с 2005 года), поэтому фиксированное
смещение надёжнее tzdata (её нет в slim-образах и на Windows без пакета).
"""

from __future__ import annotations

import datetime as dt

BISHKEK_TZ = dt.timezone(dt.timedelta(hours=6), "Asia/Bishkek")


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def now_bishkek() -> dt.datetime:
    return now_utc().astimezone(BISHKEK_TZ)


def today_bishkek() -> dt.date:
    return now_bishkek().date()


def bishkek_date(moment: dt.datetime) -> dt.date:
    """Календарная дата момента по Бишкеку (naive datetime считается UTC)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(BISHKEK_TZ).date()
