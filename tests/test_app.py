import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from budget_cockpit.app import create_app
from budget_cockpit.config import Config
from budget_cockpit.i18n import catalog

PASSWORD = "correct horse 42"


def csrf_of(html: str) -> str:
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Config(data_dir=tmp_path))) as c:
        yield c


def setup_account(client, demo=False, lang="fr"):
    token = csrf_of(client.get("/setup").text)
    data = {"csrf": token, "username": "vincent", "password": PASSWORD, "password2": PASSWORD, "lang": lang}
    if demo:
        data["demo"] = "on"
    return client.post("/setup", data=data, follow_redirects=False)


def page_csrf(client, path="/settings"):
    return csrf_of(client.get(path).text)


def add_line(client, **fields):
    data = {"csrf": page_csrf(client, "/lines/new"), "name": "Rent", "kind": "expense",
            "frequency": "monthly", "amount": "900", "active": "on"}
    data.update(fields)
    return client.post("/lines/save", data=data, follow_redirects=False)


def test_redirects_to_setup_first(client):
    r = client.get("/budget", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup"


def test_setup_then_budget(client):
    r = setup_account(client)
    assert r.status_code == 303 and r.headers["location"] == "/settings"
    r = client.get("/budget")
    assert r.status_code == 200 and "Reste (fin de mois)" in r.text
    # a second setup is not possible
    assert client.get("/setup", follow_redirects=False).headers["location"] == "/login"


def test_setup_rejects_short_password(client):
    token = csrf_of(client.get("/setup").text)
    r = client.post("/setup", data={"csrf": token, "username": "v", "password": "short",
                                    "password2": "short", "lang": "fr"})
    assert r.status_code == 400 and "trop court" in r.text


def test_demo_data_in_english(client):
    setup_account(client, demo=True, lang="en")
    r = client.get("/budget")
    assert "Seasonal rental" in r.text and "Left (end of month)" in r.text
    assert "Holiday let: year to date" in r.text


def test_projected_savings_sit_under_the_savings_lines(client):
    setup_account(client, demo=True)
    store = client.app.state.budgets.get(1)
    month = store.get_settings("2026-10").start_month
    savings_line = next(line for line in store.list_lines() if line.kind == "savings")
    headers = {"X-CSRF-Token": page_csrf(client, "/budget")}
    client.post("/budget/cell", json={"line_id": savings_line.id, "month": month, "value": "-1000"}, headers=headers)
    html = client.get("/budget").text
    savings_section = html.split('class="section section-savings"')[1].split("</tbody>")[0]
    assert "Épargne totale prévue" in savings_section
    # demo savings: 4 200 € today, the -1 000 € withdrawal replaces the 250 € monthly transfer
    assert "3 200,00 €" in savings_section
    assert "Épargne totale prévue" not in html.split('class="totals"')[1]


def test_post_without_csrf_is_rejected(client):
    setup_account(client)
    r = client.post("/lines/save", data={"name": "x", "kind": "expense", "frequency": "monthly"})
    assert r.status_code == 403
    r = client.post("/budget/cell", json={"line_id": 1, "month": "2026-10", "value": "1"})
    assert r.status_code == 403


def test_login_and_throttle(client):
    setup_account(client)
    client.post("/logout", data={"csrf": page_csrf(client)})
    assert client.get("/budget", follow_redirects=False).headers["location"] == "/login"
    token = csrf_of(client.get("/login").text)
    for _ in range(5):
        r = client.post("/login", data={"csrf": token, "username": "vincent", "password": "wrong"})
        assert r.status_code == 401
    r = client.post("/login", data={"csrf": token, "username": "vincent", "password": PASSWORD})
    assert r.status_code == 429


def test_login_success(client):
    setup_account(client)
    client.post("/logout", data={"csrf": page_csrf(client)})
    token = csrf_of(client.get("/login").text)
    r = client.post("/login", data={"csrf": token, "username": "vincent", "password": PASSWORD},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/budget"


def test_line_cell_override_and_paid(client):
    setup_account(client)
    assert add_line(client).status_code == 303
    store = client.app.state.budgets.get(1)
    line = store.list_lines()[0]
    month = store.get_settings("2026-10").start_month
    headers = {"X-CSRF-Token": page_csrf(client, "/budget")}
    r = client.post("/budget/cell", json={"line_id": line.id, "month": month, "value": "1 250,50"}, headers=headers)
    assert r.status_code == 200
    assert str(store.get_override(line.id, month).amount) == "1250.50"
    r = client.post("/budget/cell", json={"line_id": line.id, "month": month, "value": "abc"}, headers=headers)
    assert r.status_code == 400
    r = client.post("/budget/paid", json={"line_id": line.id, "month": month}, headers=headers)
    assert r.json()["paid"] is True and store.get_override(line.id, month).paid
    r = client.post("/budget/paid", json={"line_id": line.id, "month": month}, headers=headers)
    assert r.json()["paid"] is False and store.get_override(line.id, month) is None
    r = client.post("/budget/cell", json={"line_id": line.id, "month": month, "value": None}, headers=headers)
    assert r.status_code == 200 and store.get_override(line.id, month) is None


def test_update_balances_from_budget(client):
    setup_account(client)
    page = client.get("/budget").text
    assert 'name="savings_balance"' in page
    r = client.post("/budget/balance", data={"csrf": csrf_of(page), "current_balance": "2 500,75",
                                             "savings_balance": "6 568,24"}, follow_redirects=False)
    assert r.status_code == 303
    s = client.app.state.budgets.get(1).get_settings("2026-10")
    assert (str(s.current_balance), str(s.savings_balance)) == ("2500.75", "6568.24")
    # without the savings field (older page still open), the savings stay as they are
    client.post("/budget/balance", data={"csrf": page_csrf(client, "/budget"), "current_balance": "10"})
    s = client.app.state.budgets.get(1).get_settings("2026-10")
    assert (str(s.current_balance), str(s.savings_balance)) == ("10", "6568.24")


def test_settings_show_the_situation_before_month_closing(client):
    setup_account(client)
    html = client.get("/settings").text
    assert html.index('id="situation"') < html.index('id="close"')


def test_line_validation_error_is_shown(client):
    setup_account(client)
    r = add_line(client, frequency="yearly_table", yearly_table="")
    assert r.status_code == 400 and "2027 = 1200" in r.text


def test_yearly_table_line(client):
    setup_account(client)
    r = add_line(client, name="Tax provision", frequency="yearly_table", yearly_table="2027 = 1201\n2028: 1500")
    assert r.status_code == 303
    line = client.app.state.budgets.get(1).list_lines()[0]
    assert {k: str(v) for k, v in line.yearly_table.items()} == {2027: "1201", 2028: "1500"}


def test_api_requires_a_token(client):
    setup_account(client, demo=True)
    assert client.get("/api/v1/summary").status_code == 401
    assert client.get("/api/v1/summary", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = client.post("/settings/token", data={"csrf": page_csrf(client), "name": "HA"})
    token = re.search(r'<code class="token">([^<]+)</code>', r.text).group(1)
    data = client.get("/api/v1/summary", headers={"Authorization": f"Bearer {token}"}).json()
    assert data["currency"] == "EUR" and data["alert"] in ("ok", "warning", "overdraft")
    assert len(data["months"]) == 24 and "Gîte" in data["groups"]
    # the token is never stored in clear
    assert token not in Path(client.app.state.config.db_path).read_bytes().decode("latin-1")


def test_export_import_roundtrip(client, tmp_path):
    setup_account(client, demo=True)
    exported = client.get("/settings/export")
    assert exported.headers["content-disposition"].startswith("attachment")
    data = exported.json()
    assert data["format"] == "budget-cockpit" and len(data["lines"]) == 17
    with TestClient(create_app(Config(data_dir=tmp_path / "other"))) as other:
        setup_account(other)
        r = other.post("/settings/import", data={"csrf": page_csrf(other)},
                       files={"file": ("backup.json", json.dumps(data), "application/json")},
                       follow_redirects=False)
        assert r.status_code == 303
        assert len(other.app.state.budgets.get(1).list_lines()) == 17


def test_import_accepts_backups_from_before_the_rename(client):
    setup_account(client)
    legacy = {"format": "reste", "version": 1,
              "settings": {"start_month": "2026-10", "current_balance": "100"},
              "lines": [{"name": "Rent", "kind": "expense", "amount": "900", "frequency": "monthly"}]}
    r = client.post("/settings/import", data={"csrf": page_csrf(client)},
                    files={"file": ("old.json", json.dumps(legacy), "application/json")},
                    follow_redirects=False)
    assert r.status_code == 303
    assert [line.name for line in client.app.state.budgets.get(1).list_lines()] == ["Rent"]


def test_import_rejects_garbage(client):
    setup_account(client)
    r = client.post("/settings/import", data={"csrf": page_csrf(client)},
                    files={"file": ("x.json", b'{"format": "other"}', "application/json")})
    assert r.status_code == 400


def test_close_month(client):
    setup_account(client)
    store = client.app.state.budgets.get(1)
    before = store.get_settings("2026-10").start_month
    r = client.post("/settings/close-month", data={"csrf": page_csrf(client), "balance": "1 000", "savings": ""},
                    follow_redirects=False)
    assert r.status_code == 303
    after = store.get_settings("2026-10")
    assert after.start_month > before and str(after.current_balance) == "1000"


def test_security_headers(client):
    r = client.get("/setup")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "httponly" in r.headers["set-cookie"].lower()


def test_healthz(client):
    assert client.get("/healthz").json()["status"] == "ok"


def test_locales_are_complete():
    fr, en = catalog("fr"), catalog("en")
    assert set(fr) == set(en)
    templates = Path(__file__).resolve().parents[1] / "src" / "budget_cockpit" / "templates"
    used = set()
    for f in templates.glob("*.html"):
        used |= set(re.findall(r"t\('([a-z_.0-9]+)'", f.read_text(encoding="utf-8")))
    source = (Path(__file__).resolve().parents[1] / "src" / "budget_cockpit").glob("*.py")
    for f in source:
        used |= set(re.findall(r'"((?:error|flash)\.[a-z_]+)"', f.read_text(encoding="utf-8")))
    dynamic = {f"kind.{k}{s}" for k in ("income", "expense", "savings") for s in ("", "_plural")}
    dynamic |= {f"freq.{f}" for f in ("monthly", "yearly", "profile", "manual", "yearly_table")}
    dynamic |= {f"month.{i}" for i in range(1, 13)} | {"alert.warning", "alert.overdraft"}
    dynamic |= {f"budget.state_{s}" for s in ("rule", "override", "paid")}
    used = {k for k in used if not k.endswith((".", "_", ".html"))}  # dynamic prefixes, template names
    missing = sorted(k for k in used | dynamic if k not in fr)
    assert missing == []
