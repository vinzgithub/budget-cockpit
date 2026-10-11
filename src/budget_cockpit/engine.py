"""Projection engine: turns budget lines, per-month exceptions and settings
into a month-by-month forecast of the current-account balance.

All amounts are ``Decimal``. Nothing is rounded here: rounding happens only
when values are displayed or exported, so long projections never drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal

KINDS = ("income", "expense", "savings")
FREQUENCIES = ("monthly", "yearly", "profile", "manual", "yearly_table")
ZERO = Decimal("0")


# --------------------------------------------------------------------- months
def parse_month(value: str) -> tuple[int, int]:
    """'2026-10' -> (2026, 10). Raises ValueError on anything else."""
    year_s, month_s = value.strip().split("-")
    year, month = int(year_s), int(month_s)
    if not (1 <= month <= 12 and 1900 <= year <= 2200):
        raise ValueError(f"invalid month: {value!r}")
    return year, month


def format_month(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def add_months(value: str, n: int) -> str:
    year, month = parse_month(value)
    index = year * 12 + (month - 1) + n
    return format_month(index // 12, index % 12 + 1)


def month_range(start: str, count: int) -> list[str]:
    return [add_months(start, i) for i in range(count)]


# --------------------------------------------------------------------- models
@dataclass
class Line:
    """One budget line (salary, rent, loan, seasonal rental income...)."""

    id: int
    name: str
    kind: str = "expense"  # income | expense | savings
    group: str = ""  # optional free label, e.g. "Holiday home"
    amount: Decimal = ZERO
    frequency: str = "monthly"  # see FREQUENCIES
    month: int | None = None  # 1..12, for "yearly"
    start: str | None = None  # 'YYYY-MM' inclusive
    end: str | None = None  # 'YYYY-MM' inclusive
    profile: list[Decimal] = field(default_factory=lambda: [ZERO] * 12)
    yearly_table: dict[int, Decimal] = field(default_factory=dict)
    active: bool = True
    position: int = 0
    note: str = ""


@dataclass
class Override:
    """A value typed for one line in one month, replacing the rule.

    ``paid=True`` means the movement already happened: it is already reflected
    in the current balance, so it counts as zero in the forecast.
    """

    line_id: int
    month: str
    amount: Decimal = ZERO
    paid: bool = False


@dataclass
class Settings:
    start_month: str  # first month shown = current month
    horizon: int = 24  # number of months projected
    current_balance: Decimal = ZERO  # current-account balance today
    savings_balance: Decimal = ZERO  # savings already put aside
    safety_threshold: Decimal = Decimal("100")
    currency: str = "EUR"
    locale: str = "fr"


# ---------------------------------------------------------------------- rules
def yearly_table_amount(table: dict[int, Decimal], year: int) -> Decimal:
    """Annual amount for ``year``: last defined year <= ``year`` carries forward."""
    known = [y for y in table if y <= year]
    if not known:
        return ZERO
    return table[max(known)]


def rule_amount(line: Line, month: str) -> Decimal:
    """Amount produced by the line's rule for ``month`` (ignoring exceptions)."""
    if not line.active:
        return ZERO
    if line.start and month < line.start:
        return ZERO
    if line.end and month > line.end:
        return ZERO
    year, mon = parse_month(month)
    freq = line.frequency
    if freq == "monthly":
        return line.amount
    if freq == "yearly":
        return line.amount if line.month == mon else ZERO
    if freq == "profile":
        return line.profile[mon - 1] if len(line.profile) == 12 else ZERO
    if freq == "yearly_table":
        return yearly_table_amount(line.yearly_table, year) / 12
    return ZERO  # manual: only exceptions count


@dataclass
class Cell:
    amount: Decimal
    state: str  # "rule" | "override" | "paid"


@dataclass
class Projection:
    months: list[str]
    cells: dict[int, list[Cell]]
    income: list[Decimal]
    expense: list[Decimal]
    savings: list[Decimal]
    balance: list[Decimal]  # current account at end of each month
    savings_total: list[Decimal]
    alerts: list[str]  # "" | "warning" | "overdraft"
    groups: dict[str, list[Decimal]]  # group net (in - out) per month
    groups_ytd: dict[str, list[Decimal]]  # same, cumulated within each year

    @property
    def lowest(self) -> tuple[str, Decimal] | None:
        if not self.months:
            return None
        i = min(range(len(self.months)), key=lambda k: self.balance[k])
        return self.months[i], self.balance[i]

    @property
    def first_alert(self) -> tuple[str, str] | None:
        for m, a in zip(self.months, self.alerts, strict=True):
            if a:
                return m, a
        return None


def project(settings: Settings, lines: list[Line], overrides: list[Override]) -> Projection:
    months = month_range(settings.start_month, max(1, settings.horizon))
    by_key = {(o.line_id, o.month): o for o in overrides}
    n = len(months)
    income = [ZERO] * n
    expense = [ZERO] * n
    savings = [ZERO] * n
    cells: dict[int, list[Cell]] = {}
    groups: dict[str, list[Decimal]] = {}

    for line in sorted(lines, key=lambda x: (KINDS.index(x.kind) if x.kind in KINDS else 9, x.position, x.id)):
        row: list[Cell] = []
        for i, m in enumerate(months):
            o = by_key.get((line.id, m))
            if o is not None:
                cell = Cell(ZERO if o.paid else o.amount, "paid" if o.paid else "override")
            else:
                cell = Cell(rule_amount(line, m), "rule")
            row.append(cell)
            if line.kind == "income":
                income[i] += cell.amount
            elif line.kind == "savings":
                savings[i] += cell.amount
            else:
                expense[i] += cell.amount
            if line.group:
                g = groups.setdefault(line.group, [ZERO] * n)
                g[i] += cell.amount if line.kind == "income" else -cell.amount
        cells[line.id] = row

    balance, savings_total, alerts = [], [], []
    running = settings.current_balance
    saved = settings.savings_balance
    for i in range(n):
        running = running + income[i] - expense[i] - savings[i]
        saved = saved + savings[i]
        balance.append(running)
        savings_total.append(saved)
        if running < 0:
            alerts.append("overdraft")
        elif running < settings.safety_threshold:
            alerts.append("warning")
        else:
            alerts.append("")

    groups_ytd: dict[str, list[Decimal]] = {}
    for name, values in groups.items():
        acc, out, prev_year = ZERO, [], None
        for m, v in zip(months, values, strict=True):
            year = parse_month(m)[0]
            if year != prev_year:
                acc, prev_year = ZERO, year
            acc += v
            out.append(acc)
        groups_ytd[name] = out

    return Projection(months, cells, income, expense, savings, balance, savings_total,
                      alerts, groups, groups_ytd)


# -------------------------------------------------------------------- summary
@dataclass
class YearSummary:
    year: int
    months: int
    income: Decimal
    expense: Decimal
    savings: Decimal
    unallocated: Decimal  # income - expense - savings
    groups: dict[str, Decimal]

    @property
    def capacity(self) -> Decimal:
        """What can really be put aside over the year: planned savings + leftover."""
        return self.savings + self.unallocated

    @property
    def capacity_per_month(self) -> Decimal:
        return self.capacity / self.months if self.months else ZERO


def yearly_summary(p: Projection) -> list[YearSummary]:
    out: dict[int, YearSummary] = {}
    for i, m in enumerate(p.months):
        year = parse_month(m)[0]
        s = out.setdefault(year, YearSummary(year, 0, ZERO, ZERO, ZERO, ZERO, {}))
        s.months += 1
        s.income += p.income[i]
        s.expense += p.expense[i]
        s.savings += p.savings[i]
        s.unallocated += p.income[i] - p.expense[i] - p.savings[i]
        for g, values in p.groups.items():
            s.groups[g] = s.groups.get(g, ZERO) + values[i]
    return [out[y] for y in sorted(out)]


# ------------------------------------------------------------------ month end
def close_month(settings: Settings, lines: list[Line], overrides: list[Override],
                new_balance: Decimal | None = None,
                new_savings: Decimal | None = None) -> tuple[Settings, list[Override]]:
    """Move to the next month: the first month disappears, its exceptions are
    dropped, and the real balances become the new starting point.

    When a real balance is not given, the forecast end-of-month value is used.
    """
    proj = project(settings, lines, overrides)
    new = replace(
        settings,
        start_month=add_months(settings.start_month, 1),
        current_balance=new_balance if new_balance is not None else proj.balance[0],
        savings_balance=new_savings if new_savings is not None else proj.savings_total[0],
    )
    kept = [o for o in overrides if o.month >= new.start_month]
    return new, kept
