from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

import pandas as pd


FERMETURES_AERECO: List[datetime] = []


def _est_ferie_france(date: datetime) -> bool:
    y = date.year
    fixes = [
        datetime(y, 1, 1), datetime(y, 5, 1), datetime(y, 5, 8), datetime(y, 7, 14),
        datetime(y, 8, 15), datetime(y, 11, 1), datetime(y, 11, 11), datetime(y, 12, 25),
    ]
    a = y % 19; b = y // 100; c = y % 100; d = b // 4; e = b % 4
    f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4; k = c % 4; l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mo = (h + l - 7 * m + 114) // 31
    da = ((h + l - 7 * m + 114) % 31) + 1
    paques = datetime(y, mo, da)
    mobiles = [paques + timedelta(days=1), paques + timedelta(days=39), paques + timedelta(days=50)]
    return date in fixes or date in mobiles


def est_jour_ouvre(date: datetime, fermetures: List[datetime] = FERMETURES_AERECO) -> bool:
    return date.weekday() < 5 and not _est_ferie_france(date) and date not in fermetures


def soustraire_jours_ouvres(
    date: datetime, nb: int, fermetures: List[datetime] = FERMETURES_AERECO
) -> datetime:
    if pd.isna(date):
        return pd.NaT  # type: ignore[return-value]
    if isinstance(date, pd.Timestamp):
        date = date.to_pydatetime()
    r = date
    n = nb
    while n > 0:
        r -= timedelta(days=1)
        if est_jour_ouvre(r, fermetures):
            n -= 1
    return r


def generer_jours_ouvres(
    date_debut: datetime, date_fin: datetime, fermetures: List[datetime] = FERMETURES_AERECO
) -> List[datetime]:
    jours = []
    d = date_debut
    while d <= date_fin:
        if est_jour_ouvre(d, fermetures):
            jours.append(d)
        d += timedelta(days=1)
    return jours


def dernier_jour_ouvre_avant_ou_egal(
    date_cible: datetime, fermetures: List[datetime] = FERMETURES_AERECO
) -> datetime:
    if isinstance(date_cible, pd.Timestamp):
        date_cible = date_cible.to_pydatetime()
    d = date_cible
    while not est_jour_ouvre(d, fermetures):
        d -= timedelta(days=1)
    return d


def jour_ouvre_precedent(
    date_cible: datetime, fermetures: List[datetime] = FERMETURES_AERECO
) -> datetime:
    if isinstance(date_cible, pd.Timestamp):
        date_cible = date_cible.to_pydatetime()
    d = date_cible - timedelta(days=1)
    while not est_jour_ouvre(d, fermetures):
        d -= timedelta(days=1)
    return d


def to_python_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        if pd.isna(value):  # type: ignore[arg-type]
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, datetime):
        return value
    return None
