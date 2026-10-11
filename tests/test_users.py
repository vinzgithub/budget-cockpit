import json
import re
import sqlite3
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from budget_cockpit import cli
from budget_cockpit.app import create_app
from budget_cockpit.config import Config
from budget_cockpit.engine import Settings
from budget_cockpit.security import hash_password, token_hash
from budget_cockpit.store import settings_to_dict
from test_app import PASSWORD, csrf_of, page_csrf, setup_account

TEMP = "temporary pass 1"
STEPH_PASSWORD = "steph own password"


@pytest.fixture
def app(tmp_path):
    return create_app(Config(data_dir=tmp_path))


@pytest.fixture
def admin(app):
    with TestClient(app) as c:
        setup_account(c, demo=True)
        yield c


@pytest.fixture
def steph(app):
    with TestClient(app) as c:
        yield c


def confirm_admin(client, password=PASSWORD):
    token = csrf_of(client.get("/admin/confirm").text)
    return client.post("/admin/confirm", data={"csrf": token, "password": password}, follow_redirects=False)


def create_user(admin, username="steph", lang="fr", **extra):
    confirm_admin(admin)
    data = {"csrf": page_csrf(admin, "/admin"), "username": username, "password": TEMP, "password2": TEMP,
            "lang": lang, **extra}
    return admin.post("/admin/users", data=data, follow_redirects=False)


def log_in(client, username, password):
    token = csrf_of(client.get("/login").text)
    return client.post("/login", data={"csrf": token, "username": username, "password": password},
                       follow_redirects=False)


def first_login(client, username="steph"):
    """Logs in with the temporary password and picks a personal one."""
    log_in(client, username, TEMP)
    token = csrf_of(client.get("/account/password").text)
    r = client.post("/account/password", data={"csrf": token, "new": STEPH_PASSWORD, "new2": STEPH_PASSWORD},
                    follow_redirects=False)
    assert r.headers["location"] in ("/budget", "/settings")


def user_id(app, username):
    return app.state.accounts.get_user_by_name(username)["id"]


# ------------------------------------------------------------------ roles
def test_first_account_is_admin_and_must_confirm_password(admin):
    assert 'href="/admin"' in admin.get("/budget").text
    assert admin.get("/admin", follow_redirects=False).headers["location"] == "/admin/confirm"
    assert confirm_admin(admin, "wrong password").status_code == 400
    assert confirm_admin(admin).headers["location"] == "/admin"
    page = admin.get("/admin").text
    assert "vincent" in page and "Nouveau compte" in page


def test_admin_confirmation_expires(admin, clock):
    confirm_admin(admin)
    assert admin.get("/admin").status_code == 200
    clock.advance(601)
    assert admin.get("/admin", follow_redirects=False).headers["location"] == "/admin/confirm"


def test_users_cannot_reach_the_admin_pages(app, admin, steph):
    create_user(admin)
    first_login(steph)
    assert 'href="/admin"' not in steph.get("/budget").text
    assert steph.get("/admin", follow_redirects=False).status_code == 403
    assert steph.get("/admin/confirm", follow_redirects=False).status_code == 403
    r = steph.post("/admin/users", data={"csrf": page_csrf(steph, "/budget"), "username": "x",
                                         "password": TEMP, "password2": TEMP}, follow_redirects=False)
    assert r.status_code == 403 and app.state.accounts.user_count() == 2


# -------------------------------------------------------------- accounts
def test_new_user_picks_a_password_then_gets_an_empty_budget(app, admin, steph):
    r = create_user(admin, lang="en")
    assert r.headers["location"] == "/admin"
    assert "steph" in admin.get("/admin").text
    log_in(steph, "steph", TEMP)
    # nothing but the password page until a personal password is chosen
    assert steph.get("/budget", follow_redirects=False).headers["location"] == "/account/password"
    r = steph.post("/budget/cell", json={"line_id": 1, "month": "2026-10", "value": "1"},
                   headers={"X-CSRF-Token": csrf_of(steph.get("/account/password").text)})
    assert r.status_code == 403
    page = steph.get("/account/password").text
    assert "Choose your password" in page  # the language chosen by the administrator
    r = steph.post("/account/password", data={"csrf": csrf_of(page), "new": TEMP, "new2": TEMP})
    assert r.status_code == 400
    first_login(steph)
    assert "then add your lines" in steph.get("/settings").text  # welcome on an empty budget
    page = steph.get("/budget").text
    assert "Left (end of month)" in page and "Seasonal rental" not in page
    assert app.state.budgets.get(user_id(app, "steph")).list_lines() == []
    # the temporary password no longer works
    steph.post("/logout", data={"csrf": page_csrf(steph, "/budget")})
    assert log_in(steph, "steph", TEMP).status_code == 401
    assert log_in(steph, "steph", STEPH_PASSWORD).headers["location"] == "/budget"


def test_new_user_with_demo_data(app, admin, steph):
    create_user(admin, demo="on")
    first_login(steph)
    assert len(app.state.budgets.get(user_id(app, "steph")).list_lines()) == 17


def test_usernames_are_unique_whatever_the_case(admin):
    create_user(admin)
    r = create_user(admin, username="Steph")
    assert r.status_code == 400 and "existe déjà" in r.text


def test_budgets_are_separate(app, admin, steph, tmp_path):
    create_user(admin)
    first_login(steph)
    admin_lines = app.state.budgets.get(1).list_lines()
    assert len(admin_lines) == 17
    # line ids belong to each budget: Steph's budget has no line 1
    assert steph.get("/lines/1/edit").status_code == 404
    headers = {"X-CSRF-Token": page_csrf(steph, "/budget")}
    r = steph.post("/budget/cell", json={"line_id": 1, "month": "2026-10", "value": "999"}, headers=headers)
    assert r.status_code == 400
    steph.post("/lines/1/delete", data={"csrf": headers["X-CSRF-Token"]})
    steph.post("/budget/balance", data={"csrf": headers["X-CSRF-Token"], "current_balance": "42"})
    r = steph.post("/lines/save", data={"csrf": page_csrf(steph, "/lines/new"), "name": "Steph rent",
                                        "kind": "expense", "frequency": "monthly", "amount": "700",
                                        "active": "on"}, follow_redirects=False)
    assert r.status_code == 303
    # the administrator's budget is untouched
    assert [ln.name for ln in app.state.budgets.get(1).list_lines()] == [ln.name for ln in admin_lines]
    assert app.state.budgets.get(1).get_settings("2026-10").current_balance != Decimal("42")
    assert "Steph rent" not in admin.get("/budget").text and "Steph rent" in steph.get("/budget").text
    # one database file each
    assert sorted(p.name for p in (tmp_path / "budgets").glob("*.db")) == ["user-1.db", "user-2.db"]
    # exports carry only one's own budget
    exported = steph.get("/settings/export")
    assert "steph" in exported.headers["content-disposition"]
    assert [ln["name"] for ln in exported.json()["lines"]] == ["Steph rent"]


def test_api_keys_read_their_owners_budget(app, admin, steph):
    create_user(admin)
    first_login(steph)
    steph.post("/budget/balance", data={"csrf": page_csrf(steph, "/budget"), "current_balance": "42"})

    def new_key(client):
        r = client.post("/settings/token", data={"csrf": page_csrf(client), "name": "HA"})
        return re.search(r'<code class="token">([^<]+)</code>', r.text).group(1)

    steph_key, admin_key = new_key(steph), new_key(admin)
    s = steph.get("/api/v1/summary", headers={"Authorization": f"Bearer {steph_key}"}).json()
    a = admin.get("/api/v1/summary", headers={"Authorization": f"Bearer {admin_key}"}).json()
    assert s["groups"] == {} and "Gîte" in a["groups"]
    # Steph cannot revoke the administrator's key (id 2 is the admin's)
    admin_key_id = app.state.accounts.list_tokens(1)[0]["id"]
    steph.post(f"/settings/token/{admin_key_id}/revoke", data={"csrf": page_csrf(steph)})
    assert admin.get("/api/v1/summary", headers={"Authorization": f"Bearer {admin_key}"}).status_code == 200
    assert len(app.state.accounts.list_tokens(user_id(app, "steph"))) == 1


# ---------------------------------------------------------- admin actions
def test_reset_password_logs_the_user_out(app, admin, steph):
    create_user(admin)
    first_login(steph)
    uid = user_id(app, "steph")
    new_temp = "another temp 22"
    r = admin.post(f"/admin/users/{uid}/password", data={"csrf": page_csrf(admin, "/admin"), "password": new_temp,
                                                         "password2": new_temp}, follow_redirects=False)
    assert r.status_code == 303
    assert steph.get("/budget", follow_redirects=False).headers["location"] == "/login"
    assert log_in(steph, "steph", STEPH_PASSWORD).status_code == 401
    log_in(steph, "steph", new_temp)
    assert steph.get("/budget", follow_redirects=False).headers["location"] == "/account/password"


def test_admin_turns_off_a_users_2fa(app, admin, steph):
    create_user(admin)
    first_login(steph)
    uid = user_id(app, "steph")
    app.state.accounts.start_totp_setup(uid, "JBSWY3DPEHPK3PXP")
    app.state.accounts.enable_totp(uid, 0, [])
    r = admin.post(f"/admin/users/{uid}/totp-off", data={"csrf": page_csrf(admin, "/admin")}, follow_redirects=False)
    assert r.status_code == 303 and not app.state.accounts.totp_enabled(uid)
    assert log_in(steph, "steph", STEPH_PASSWORD).headers["location"] == "/budget"


def test_role_change(app, admin):
    create_user(admin)
    uid = user_id(app, "steph")
    admin.post(f"/admin/users/{uid}/role", data={"csrf": page_csrf(admin, "/admin")})
    assert app.state.accounts.get_user(uid)["is_admin"] == 1
    admin.post(f"/admin/users/{uid}/role", data={"csrf": page_csrf(admin, "/admin")})
    assert app.state.accounts.get_user(uid)["is_admin"] == 0


def test_admin_cannot_demote_or_delete_themself(app, admin):
    confirm_admin(admin)
    token = page_csrf(admin, "/admin")
    assert admin.post("/admin/users/1/role", data={"csrf": token}).status_code == 400
    assert admin.post("/admin/users/1/delete", data={"csrf": token, "confirm": "vincent"}).status_code == 400
    assert admin.post("/admin/users/1/password", data={"csrf": token, "password": TEMP,
                                                       "password2": TEMP}).status_code == 400
    assert app.state.accounts.get_user(1)["is_admin"] == 1


def test_delete_user_removes_everything(app, admin, steph, tmp_path):
    create_user(admin)
    first_login(steph)
    uid = user_id(app, "steph")
    key = re.search(r'<code class="token">([^<]+)</code>',
                    steph.post("/settings/token", data={"csrf": page_csrf(steph), "name": "HA"}).text).group(1)
    token = page_csrf(admin, "/admin")
    r = admin.post(f"/admin/users/{uid}/delete", data={"csrf": token, "confirm": "stef"})
    assert r.status_code == 400 and app.state.accounts.get_user(uid) is not None
    r = admin.post(f"/admin/users/{uid}/delete", data={"csrf": token, "confirm": "steph"}, follow_redirects=False)
    assert r.status_code == 303
    assert app.state.accounts.get_user(uid) is None
    assert not (tmp_path / "budgets" / f"user-{uid}.db").exists()
    assert steph.get("/budget", follow_redirects=False).headers["location"] == "/login"
    assert steph.get("/api/v1/summary", headers={"Authorization": f"Bearer {key}"}).status_code == 401
    assert len(app.state.budgets.get(1).list_lines()) == 17


def test_cli_make_admin(app, admin, tmp_path, monkeypatch):
    create_user(admin)
    monkeypatch.setenv("BUDGET_COCKPIT_DATA_DIR", str(tmp_path))
    assert cli.main(["make-admin", "steph"]) == 0
    assert app.state.accounts.get_user(user_id(app, "steph"))["is_admin"] == 1


# -------------------------------------------------------------- migration
LEGACY_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
    pw_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE lines (id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
    grp TEXT NOT NULL DEFAULT '', amount TEXT NOT NULL DEFAULT '0',
    frequency TEXT NOT NULL, month INTEGER, start TEXT, end_month TEXT,
    profile TEXT NOT NULL DEFAULT '[]', yearly_table TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1, position INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '');
CREATE TABLE overrides (line_id INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
    month TEXT NOT NULL, amount TEXT NOT NULL, paid INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (line_id, month));
CREATE TABLE api_tokens (id INTEGER PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL, last_used TEXT);
"""


def make_legacy_database(path):
    """A database as written by version 0.1: one user, one budget in the same file."""
    db = sqlite3.connect(path)
    db.executescript(LEGACY_SCHEMA)
    settings = Settings(start_month="2026-10", current_balance=Decimal("668.56"), savings_balance=Decimal("6068"))
    db.executemany("INSERT INTO meta VALUES(?, ?)", [("schema_version", "1"),
                                                      ("settings", json.dumps(settings_to_dict(settings)))])
    db.execute("INSERT INTO users VALUES(1, 'vincent', ?, '2026-10-11T10:00:00+00:00')", (hash_password(PASSWORD),))
    db.execute("INSERT INTO lines(id, name, kind, amount, frequency, position) "
               "VALUES(3, 'Salaire', 'income', '3500', 'monthly', 1)")
    db.execute("INSERT INTO lines(id, name, kind, amount, frequency, position) "
               "VALUES(7, 'Loyer', 'expense', '1017.67', 'monthly', 2)")
    db.execute("INSERT INTO overrides VALUES(3, '2026-10', '0', 1)")
    db.execute("INSERT INTO api_tokens(name, token_hash, created_at) VALUES('HA', ?, '2026-10-11')",
               (token_hash("legacy-key"),))
    db.commit()
    db.close()


def test_migration_from_a_single_user_database(tmp_path):
    make_legacy_database(tmp_path / "budget_cockpit.db")
    for _ in range(2):  # starting again changes nothing
        app = create_app(Config(data_dir=tmp_path))
        with TestClient(app) as c:
            assert app.state.accounts.get_user(1)["is_admin"] == 1
            budget = app.state.budgets.get(1)
            assert [(ln.id, ln.name) for ln in budget.list_lines()] == [(3, "Salaire"), (7, "Loyer")]
            assert [(o.line_id, o.month, o.paid) for o in budget.list_overrides()] == [(3, "2026-10", True)]
            assert budget.get_settings("2026-10").current_balance == Decimal("668.56")
            data = c.get("/api/v1/summary", headers={"Authorization": "Bearer legacy-key"}).json()
            assert data["balance_end_of_month"] == round(668.56 - 1017.67, 2)
            assert log_in(c, "vincent", PASSWORD).headers["location"] == "/budget"
            assert "Loyer" in c.get("/budget").text
        app.state.budgets.close()
        app.state.accounts.close()
    db = sqlite3.connect(tmp_path / "budget_cockpit.db")
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "lines" not in tables and "overrides" not in tables
    assert db.execute("SELECT value FROM meta WHERE key = 'settings'").fetchone() is None
    db.close()
