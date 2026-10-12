# Copyright © 2026 Vincent Fournet
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Translations (fr / en) and locale-aware formatting of money and months."""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path

LOCALES = ("fr", "en")
_DIR = Path(__file__).parent / "locales"
SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF"}
MONTHS = {
    "fr": ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
}
MONTHS_LONG = {
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
           "octobre", "novembre", "décembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"],
}


@cache
def catalog(lang: str) -> dict[str, str]:
    return json.loads((_DIR / f"{lang}.json").read_text(encoding="utf-8"))


def t(lang: str, key: str, **kw) -> str:
    text = catalog(lang if lang in LOCALES else "fr").get(key) or catalog("en").get(key) or key
    return text.format(**kw) if kw else text


def pick_locale(accept_language: str | None, default: str = "fr") -> str:
    if not accept_language:
        return default
    for part in accept_language.split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in LOCALES:
            return code
    return default


def _group(int_part: str, sep: str) -> str:
    out = []
    while len(int_part) > 3:
        out.insert(0, int_part[-3:])
        int_part = int_part[:-3]
    out.insert(0, int_part)
    return sep.join(out)


def fmt_number(value: Decimal, lang: str, decimals: int = 2) -> str:
    q = Decimal(1).scaleb(-decimals)
    v = Decimal(value).quantize(q, rounding=ROUND_HALF_UP)
    if v == 0:
        v = abs(v)
    sign = "−" if v < 0 else ""
    int_part, _, frac = f"{abs(v):.{decimals}f}".partition(".")
    if lang == "fr":
        body = _group(int_part, " ") + ("," + frac if decimals else "")
    else:
        body = _group(int_part, ",") + ("." + frac if decimals else "")
    return sign + body


def fmt_money(value: Decimal, lang: str, currency: str = "EUR", decimals: int = 2) -> str:
    sym = SYMBOLS.get(currency, currency)
    num = fmt_number(value, lang, decimals)
    if lang == "fr" or sym == "CHF":
        return f"{num} {sym}"
    if num.startswith("−"):
        return f"−{sym}{num[1:]}"
    return f"{sym}{num}"


def fmt_month(month: str, lang: str, long: bool = False) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    names = MONTHS_LONG if long else MONTHS
    return f"{names.get(lang, names['en'])[mon - 1]} {year}"


def plain_amount(value: Decimal) -> str:
    """Neutral value used in form fields: 1234.56 (no grouping)."""
    v = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    s = f"{v:f}"
    return s[:-3] if s.endswith(".00") else s
