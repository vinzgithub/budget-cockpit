from decimal import Decimal as D

from budget_cockpit.engine import (
    Line,
    Override,
    Settings,
    add_months,
    close_month,
    project,
    rule_amount,
    yearly_summary,
)


def settings(**kw):
    base = dict(start_month="2026-10", horizon=12, current_balance=D("1000"),
                savings_balance=D("500"), safety_threshold=D("100"))
    base.update(kw)
    return Settings(**base)


def test_add_months_crosses_years():
    assert add_months("2026-11", 2) == "2027-01"
    assert add_months("2027-01", -1) == "2026-12"


def test_rules():
    monthly = Line(1, "Rent", "expense", amount=D("900"))
    yearly = Line(2, "Tax", "expense", amount=D("600"), frequency="yearly", month=12)
    profile = Line(3, "Gite", "income", frequency="profile",
                   profile=[D(x) for x in (0, 0, 0, 0, 0, 1000, 2000, 2000, 0, 0, 0, 0)])
    bounded = Line(4, "Laptop", "expense", amount=D("150"), start="2026-11", end="2026-12")
    manual = Line(5, "Bonus", "income", frequency="manual", amount=D("999"))
    table = Line(6, "Provision", "expense", frequency="yearly_table",
                 yearly_table={2027: D("1200"), 2029: D("2400")})
    assert rule_amount(monthly, "2026-10") == D("900")
    assert rule_amount(yearly, "2026-12") == D("600") and rule_amount(yearly, "2026-11") == 0
    assert rule_amount(profile, "2027-07") == D("2000")
    assert rule_amount(bounded, "2026-10") == 0 and rule_amount(bounded, "2026-12") == D("150")
    assert rule_amount(bounded, "2027-01") == 0
    assert rule_amount(manual, "2026-10") == 0
    assert rule_amount(table, "2026-12") == 0
    assert rule_amount(table, "2027-03") == D("100")
    assert rule_amount(table, "2028-03") == D("100")  # 2027 carries forward
    assert rule_amount(table, "2031-03") == D("200")


def test_inactive_line_is_zero():
    assert rule_amount(Line(1, "Old", amount=D("50"), active=False), "2026-10") == 0


def test_balance_savings_and_alerts():
    lines = [
        Line(1, "Salary", "income", amount=D("2000")),
        Line(2, "Rent", "expense", amount=D("1950")),
        Line(3, "Savings", "savings", frequency="manual"),
    ]
    overrides = [Override(3, "2026-11", D("200")), Override(1, "2026-10", D("0"), paid=True)]
    p = project(settings(horizon=3), lines, overrides)
    # Oct: salary already received (paid) -> 1000 - 1950 = -950
    assert p.balance[0] == D("-950") and p.alerts[0] == "overdraft"
    # Nov: +2000 -1950 -200 -> -1100
    assert p.balance[1] == D("-1100")
    assert p.savings_total[1] == D("700")
    assert p.cells[1][0].state == "paid" and p.cells[3][1].state == "override"
    assert p.lowest == ("2026-11", D("-1100"))
    assert p.first_alert == ("2026-10", "overdraft")


def test_warning_below_threshold():
    lines = [Line(1, "Spend", "expense", amount=D("950"))]
    p = project(settings(horizon=1), lines, [])
    assert p.balance[0] == D("50") and p.alerts[0] == "warning"


def test_groups_and_ytd_reset_in_january():
    lines = [
        Line(1, "Rental", "income", group="House", amount=D("1000")),
        Line(2, "Loan", "expense", group="House", amount=D("1200")),
    ]
    p = project(settings(horizon=5), lines, [])
    assert p.groups["House"][0] == D("-200")
    assert p.groups_ytd["House"][2] == D("-600")  # Oct, Nov, Dec
    assert p.groups_ytd["House"][3] == D("-200")  # January restarts


def test_yearly_summary_capacity():
    lines = [Line(1, "Salary", "income", amount=D("3000")),
             Line(2, "Costs", "expense", amount=D("2000")),
             Line(3, "Savings", "savings", amount=D("300"))]
    years = yearly_summary(project(settings(horizon=15), lines, []))
    assert [y.year for y in years] == [2026, 2027]
    y2027 = years[1]
    assert y2027.months == 12
    assert y2027.capacity == D("12000") and y2027.capacity_per_month == D("1000")


def test_yearly_table_is_not_rounded_monthly():
    # 1201 / 12 is not a whole number of cents: no drift over a year
    line = Line(1, "Provision", "expense", frequency="yearly_table", yearly_table={2027: D("1201")})
    p = project(settings(start_month="2027-01", horizon=12, current_balance=D("0"),
                         safety_threshold=D("0")), [line], [])
    assert p.balance[-1].quantize(D("0.01")) == D("-1201.00")


def test_close_month_uses_real_balances_and_drops_old_exceptions():
    lines = [Line(1, "Salary", "income", amount=D("2000"))]
    overrides = [Override(1, "2026-10", D("0"), paid=True), Override(1, "2026-12", D("2500"))]
    new, kept = close_month(settings(), lines, overrides, new_balance=D("1234"), new_savings=D("600"))
    assert new.start_month == "2026-11"
    assert new.current_balance == D("1234") and new.savings_balance == D("600")
    assert [o.month for o in kept] == ["2026-12"]


def test_close_month_defaults_to_forecast():
    lines = [Line(1, "Salary", "income", amount=D("2000"))]
    new, _ = close_month(settings(), lines, [])
    assert new.current_balance == D("3000")
