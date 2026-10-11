"""Charts drawn on the server as SVG: no chart library, nothing loaded from elsewhere.

Colours come from CSS classes (light and dark themes in app.css). Each month has an
invisible hit area carrying its tooltip as JSON; app.js shows it on hover and focus.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from markupsafe import Markup, escape

from .engine import Projection

WIDTH, HEIGHT = 1000, 280
LEFT, RIGHT, TOP, BOTTOM = 80, 84, 24, 34
CHAR_W = 7.6  # average width of a 14px label, to keep labels clear of the line
RANGES = ("12", "24", "all")


@dataclass
class Fmt:
    """What the charts need from the page: translations, money and month formatting."""

    t: Callable[..., str]
    money: Callable[..., str]
    month: Callable[..., str]
    lang: str


def months_shown(choice: str | None, available: int, default: str) -> tuple[str, int]:
    choice = choice if choice in RANGES else default
    return choice, available if choice == "all" else min(int(choice), available)


# ----------------------------------------------------------------- scales
def nice_ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    if hi - lo < 1:  # a flat line (an empty budget): show a small range around it
        hi = lo + 100
    raw = max((hi - lo) / count, 1.0)  # whole euros
    power = 10 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1, 2, 2.5, 5, 10) if m * power >= raw)
    start = math.floor(lo / step) * step
    end = math.ceil(hi / step) * step
    ticks, v = [], start
    while v <= end + step / 2:
        ticks.append(round(v, 6))
        v += step
    return ticks


class _Frame:
    def __init__(self, n: int, values: list[float]):
        self.n = n
        self.ticks = nice_ticks(min(values), max(values))
        self.lo, self.hi = self.ticks[0], self.ticks[-1]
        self.plot_w = WIDTH - LEFT - RIGHT
        self.plot_h = HEIGHT - TOP - BOTTOM
        self.slot = self.plot_w / n

    def x(self, i: int) -> float:
        return LEFT + self.slot * (i + 0.5)

    def y(self, v: float) -> float:
        return TOP + (self.hi - v) / (self.hi - self.lo) * self.plot_h


def _num(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _short_month(fmt: Fmt, month: str, first: bool) -> str:
    name, year = fmt.month(month).rsplit(" ", 1)
    return f"{name} {year[2:]}" if first or month.endswith("-01") else name


# ---------------------------------------------------------------- drawing
def _bar_path(x: float, w: float, y0: float, y1: float) -> str:
    """Bar from the baseline y0 to y1, square at the baseline, 4px rounded at the data end."""
    h = abs(y1 - y0)
    if h < 0.5:
        return ""
    r = min(4.0, w / 2, h)
    left, right = x - w / 2, x + w / 2
    if y1 < y0:  # grows upwards
        return (f"M{_num(left)},{_num(y0)} V{_num(y1 + r)} Q{_num(left)},{_num(y1)} {_num(left + r)},{_num(y1)} "
                f"H{_num(right - r)} Q{_num(right)},{_num(y1)} {_num(right)},{_num(y1 + r)} V{_num(y0)} Z")
    return (f"M{_num(left)},{_num(y0)} V{_num(y1 - r)} Q{_num(left)},{_num(y1)} {_num(left + r)},{_num(y1)} "
            f"H{_num(right - r)} Q{_num(right)},{_num(y1)} {_num(right)},{_num(y1 - r)} V{_num(y0)} Z")


def _svg(fmt: Fmt, months: list[str], frame: _Frame, body: list[str], tips: list[dict], aria: str) -> Markup:
    out = [f'<svg class="chart-svg" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{escape(aria)}">']
    # gridlines and y-axis labels (recessive, solid hairlines)
    for v in frame.ticks:
        y = _num(frame.y(v))
        cls = "axis-zero" if v == 0 else "grid"
        out.append(f'<line class="{cls}" x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{y}" y2="{y}"/>')
        out.append(f'<text class="tick" x="{LEFT - 8}" y="{y}" dy="0.32em" text-anchor="end">'
                   f'{escape(fmt.money(Decimal(str(v)), 0))}</text>')
    out.extend(body)
    # x-axis labels: at most about twelve
    every = max(1, math.ceil(frame.n / 12))
    for i, m in enumerate(months):
        if i % every == 0:
            out.append(f'<text class="tick" x="{_num(frame.x(i))}" y="{HEIGHT - 10}" text-anchor="middle">'
                       f'{escape(_short_month(fmt, m, i == 0))}</text>')
    # hover layer: a crosshair and one hit area per month, wider than the marks
    out.append(f'<line class="crosshair" x1="0" x2="0" y1="{TOP}" y2="{HEIGHT - BOTTOM}"/>')
    for i, tip in enumerate(tips):
        label = tip["t"] + " — " + " · ".join(f"{r[1]} {r[2]}" for r in tip["rows"])
        out.append(f'<rect class="hit" x="{_num(LEFT + frame.slot * i)}" y="{TOP}" width="{_num(frame.slot)}" '
                   f'height="{_num(frame.plot_h)}" data-i="{i}" data-x="{_num(frame.x(i))}" tabindex="0" '
                   f'aria-label="{escape(label)}" data-tip="{escape(json.dumps(tip, ensure_ascii=False))}"/>')
    out.append("</svg>")
    # Every piece of text above went through escape(); the rest is numbers and fixed markup.
    return Markup("".join(out))  # noqa: S704


def _line(frame: _Frame, values: list[float], cls: str) -> str:
    pts = " L".join(f"{_num(frame.x(i))},{_num(frame.y(v))}" for i, v in enumerate(values))
    return f'<path class="line {cls}" d="M{pts}"/>'


def _end_dot(frame: _Frame, i: int, v: float, cls: str) -> str:
    return f'<circle class="end-dot {cls}" cx="{_num(frame.x(i))}" cy="{_num(frame.y(v))}" r="4"/>'


def _end_note(frame: _Frame, v: float, text: str) -> str:
    """The last value, in the right margin beside the end dot: never on top of the line."""
    return (f'<text class="note" x="{_num(frame.x(frame.n - 1) + 10)}" y="{_num(frame.y(v))}" dy="0.32em">'
            f'{escape(text)}</text>')


def _clear_note(frame: _Frame, i: int, values: list[float], text: str) -> str:
    """A label for point i, above every point of the line it spans (or below if there is no room)."""
    width = len(text) * CHAR_W
    anchor = "start" if i < frame.n * 0.2 else "end" if i > frame.n * 0.8 else "middle"
    x = frame.x(i) + (-8 if anchor == "start" else 8 if anchor == "end" else 0)
    left = x if anchor == "start" else x - width if anchor == "end" else x - width / 2
    covered = [k for k in range(frame.n) if left - frame.slot / 2 <= frame.x(k) <= left + width + frame.slot / 2]
    top = min(frame.y(values[k]) for k in covered or [i])
    y = top - 12
    if y < TOP + 4:
        y = max(frame.y(values[k]) for k in covered or [i]) + 22
    return f'<text class="note" x="{_num(x)}" y="{_num(y)}" text-anchor="{anchor}">{escape(text)}</text>'


def _note(frame: _Frame, i: int, v: float, text: str, above: bool) -> str:
    anchor = "start" if i < frame.n * 0.15 else "end" if i > frame.n * 0.85 else "middle"
    dx = 6 if anchor == "start" else -6 if anchor == "end" else 0
    y = frame.y(v) + (-12 if above else 20)
    return (f'<text class="note" x="{_num(frame.x(i) + dx)}" y="{_num(y)}" text-anchor="{anchor}">'
            f'{escape(text)}</text>')


# ------------------------------------------------------------------ charts
def balance_chart(p: Projection, threshold: Decimal, n: int, fmt: Fmt) -> Markup:
    """The current account at the end of each month, against the safety threshold."""
    months = p.months[:n]
    bal = [float(b) for b in p.balance[:n]]
    th = float(threshold)
    frame = _Frame(n, [*bal, 0.0, th])
    body: list[str] = []
    zero_y, bottom_y = frame.y(0), HEIGHT - BOTTOM
    if th > 0:  # the band between zero and the threshold, then the overdraft zone under zero
        body.append(f'<rect class="band-warn" x="{LEFT}" y="{_num(frame.y(th))}" width="{_num(frame.plot_w)}" '
                    f'height="{_num(zero_y - frame.y(th))}"/>')
    if frame.lo < 0:
        body.append(f'<rect class="band-bad" x="{LEFT}" y="{_num(zero_y)}" width="{_num(frame.plot_w)}" '
                    f'height="{_num(bottom_y - zero_y)}"/>')
    ty = _num(frame.y(th))
    body.append(f'<line class="threshold" x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{ty}" y2="{ty}"/>')
    body.append(_line(frame, bal, "s-balance"))
    for i, a in enumerate(p.alerts[:n]):
        x, y = frame.x(i), frame.y(bal[i])
        if a == "overdraft":  # a diamond, so the state never rests on colour alone
            body.append(f'<path class="mark m-overdraft" d="M{_num(x)},{_num(y - 6)} L{_num(x + 6)},{_num(y)} '
                        f'L{_num(x)},{_num(y + 6)} L{_num(x - 6)},{_num(y)} Z"/>')
        elif a == "warning":
            body.append(f'<circle class="mark m-warning" cx="{_num(x)}" cy="{_num(y)}" r="4.5"/>')
    low = min(range(n), key=lambda k: bal[k])
    body.append(_clear_note(frame, low, bal, f"{fmt.money(p.balance[low])} · {fmt.month(months[low])}"))
    if low != n - 1:
        body.append(_end_dot(frame, n - 1, bal[-1], "s-balance"))
        body.append(_end_note(frame, bal[-1], fmt.money(p.balance[n - 1], 0)))
    tips = []
    for i, m in enumerate(months):
        rows = [["s-balance", fmt.t("budget.balance"), fmt.money(p.balance[i])],
                ["", fmt.t("budget.total_income"), fmt.money(p.income[i])],
                ["", fmt.t("budget.total_expense"), fmt.money(p.expense[i])]]
        if p.savings[i]:
            rows.append(["", fmt.t("budget.total_savings"), fmt.money(p.savings[i])])
        if p.alerts[i]:
            rows.append([f"m-{p.alerts[i]}", fmt.t("alert." + p.alerts[i]), ""])
        tips.append({"t": fmt.month(m, True), "rows": rows})
    aria = fmt.t("chart.balance_aria", start=fmt.month(months[0], True), end=fmt.month(months[-1], True),
                 low=fmt.money(p.balance[low]), month=fmt.month(months[low], True))
    return _svg(fmt, months, frame, body, tips, aria)


def _diverging_bars(frame: _Frame, values: list[float]) -> list[str]:
    w = min(24.0, frame.slot * 0.62)
    y0 = frame.y(0)
    out = []
    for i, v in enumerate(values):
        path = _bar_path(frame.x(i), w, y0, frame.y(v))
        if path:
            out.append(f'<path class="bar {"b-pos" if v >= 0 else "b-neg"}" data-i="{i}" d="{path}"/>')
    return out


def flow_chart(p: Projection, n: int, fmt: Fmt) -> Markup:
    """What each month adds to, or takes from, the current account."""
    months = p.months[:n]
    net = [p.income[i] - p.expense[i] - p.savings[i] for i in range(n)]
    values = [float(v) for v in net]
    frame = _Frame(n, [*values, 0.0])
    body = _diverging_bars(frame, values)
    hi, lo = max(range(n), key=lambda k: values[k]), min(range(n), key=lambda k: values[k])
    if values[hi] > 0:
        body.append(_note(frame, hi, values[hi], f"{fmt.money(net[hi], 0)} · {fmt.month(months[hi])}", above=True))
    if values[lo] < 0:
        body.append(_note(frame, lo, values[lo], f"{fmt.money(net[lo], 0)} · {fmt.month(months[lo])}", above=False))
    tips = [{"t": fmt.month(m, True), "rows": [
        ["b-pos" if net[i] >= 0 else "b-neg", fmt.t("chart.month_net"), fmt.money(net[i])],
        ["", fmt.t("budget.total_income"), fmt.money(p.income[i])],
        ["", fmt.t("budget.total_expense"), fmt.money(p.expense[i])],
        ["", fmt.t("budget.total_savings"), fmt.money(p.savings[i])]]} for i, m in enumerate(months)]
    aria = fmt.t("chart.flow_aria", start=fmt.month(months[0], True), end=fmt.month(months[-1], True))
    return _svg(fmt, months, frame, body, tips, aria)


def savings_chart(p: Projection, n: int, fmt: Fmt) -> Markup:
    """Savings put aside, month after month, with the planned transfers and withdrawals."""
    months = p.months[:n]
    values = [float(v) for v in p.savings_total[:n]]
    frame = _Frame(n, [*values, 0.0])
    body = [_line(frame, values, "s-savings"),
            _end_dot(frame, n - 1, values[-1], "s-savings"),
            _end_note(frame, values[-1], fmt.money(p.savings_total[n - 1], 0))]
    tips = [{"t": fmt.month(m, True), "rows": [
        ["s-savings", fmt.t("budget.savings_total"), fmt.money(p.savings_total[i])],
        ["", fmt.t("budget.total_savings"), fmt.money(p.savings[i])]]} for i, m in enumerate(months)]
    aria = fmt.t("chart.savings_aria", start=fmt.money(p.savings_total[0]), end=fmt.money(p.savings_total[n - 1]),
                 month=fmt.month(months[-1], True))
    return _svg(fmt, months, frame, body, tips, aria)


def group_chart(p: Projection, group: str, n: int, fmt: Fmt) -> Markup:
    """A group (a rented house, say): result of each month and running total of the year."""
    months = p.months[:n]
    month_values = p.groups[group][:n]
    ytd = p.groups_ytd[group][:n]
    bars, line = [float(v) for v in month_values], [float(v) for v in ytd]
    frame = _Frame(n, [*bars, *line, 0.0])
    body = _diverging_bars(frame, bars)
    body.append(_line(frame, line, "s-cumul"))
    body.append(_end_dot(frame, n - 1, line[-1], "s-cumul"))
    body.append(_end_note(frame, line[-1], fmt.money(ytd[-1], 0)))
    tips = [{"t": fmt.month(m, True), "rows": [
        ["b-pos" if month_values[i] >= 0 else "b-neg", fmt.t("chart.month_result"), fmt.money(month_values[i])],
        ["s-cumul", fmt.t("chart.year_to_date"), fmt.money(ytd[i])]]} for i, m in enumerate(months)]
    aria = fmt.t("chart.group_aria", group=group, start=fmt.month(months[0], True), end=fmt.month(months[-1], True))
    return _svg(fmt, months, frame, body, tips, aria)
