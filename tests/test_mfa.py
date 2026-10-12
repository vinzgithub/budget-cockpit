# Copyright © 2026 Vincent Fournet
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import base64
import re

import pytest
from fastapi.testclient import TestClient

from budget_cockpit import cli
from budget_cockpit.app import create_app
from budget_cockpit.config import Config
from budget_cockpit.security import totp_at, totp_match, totp_step, totp_uri
from test_app import PASSWORD, csrf_of, page_csrf, setup_account

# RFC 6238, appendix B (SHA-1, 8 digits)
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()
RFC_VECTORS = [(59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
               (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130")]
CODE_RE = re.compile(r"<code>([a-z2-9]{5}-[a-z2-9]{5})</code>")


@pytest.fixture
def app(tmp_path):
    return create_app(Config(data_dir=tmp_path))


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def code_now(secret, clock, offset=0):
    return totp_at(secret, totp_step(clock.now) + offset)


def enable_mfa(client, clock):
    """Turns two-factor authentication on; returns the secret and the recovery codes."""
    r = client.post("/settings/totp/setup", data={"csrf": page_csrf(client)}, follow_redirects=False)
    assert r.headers["location"] == "/settings/totp"
    page = client.get("/settings/totp")
    assert "data:image/svg+xml" in page.text
    secret = client.app.state.accounts.get_totp(1)["secret"]
    assert secret.replace(" ", "")[:4] in page.text
    r = client.post("/settings/totp/enable",
                    data={"csrf": csrf_of(page.text), "code": code_now(secret, clock), "password": PASSWORD})
    assert r.status_code == 200
    codes = CODE_RE.findall(r.text)
    assert len(codes) == 10 and len(set(codes)) == 10
    return secret, codes


def log_out(client):
    client.post("/logout", data={"csrf": page_csrf(client)})


def log_in_password(client):
    token = csrf_of(client.get("/login").text)
    return client.post("/login", data={"csrf": token, "username": "vincent", "password": PASSWORD},
                       follow_redirects=False)


def send_code(client, code):
    token = csrf_of(client.get("/login/code").text)
    return client.post("/login/code", data={"csrf": token, "code": code}, follow_redirects=False)


# ------------------------------------------------------------- algorithm
@pytest.mark.parametrize(("at", "expected"), RFC_VECTORS)
def test_totp_matches_rfc_6238(at, expected):
    assert totp_at(RFC_SECRET, at // 30, digits=8) == expected


def test_totp_accepts_neighbour_steps_once():
    secret = RFC_SECRET
    now = 1_800_000_000
    step = totp_step(now)
    assert totp_match(secret, totp_at(secret, step), step - 1, now) == step
    assert totp_match(secret, totp_at(secret, step - 1), step - 2, now) == step - 1
    assert totp_match(secret, totp_at(secret, step + 1), 0, now) == step + 1
    assert totp_match(secret, totp_at(secret, step + 2), 0, now) is None
    # already used: neither this code nor an older one works again
    assert totp_match(secret, totp_at(secret, step), step, now) is None
    assert totp_match(secret, totp_at(secret, step - 1), step, now) is None
    code = totp_at(secret, step)
    assert totp_match(secret, f"{code[:3]} {code[3:]}", step - 1, now) == step  # spaces are fine
    assert totp_match(secret, "abcdef", 0, now) is None


def test_totp_uri():
    uri = totp_uri("JBSWY3DPEHPK3PXP", "vincent")
    assert uri.startswith("otpauth://totp/Budget%20Cockpit%3Avincent?secret=JBSWY3DPEHPK3PXP&")
    assert "issuer=Budget%20Cockpit" in uri and "digits=6" in uri and "period=30" in uri


# ----------------------------------------------------------------- flows
def test_login_asks_for_a_code_once_enabled(client, clock):
    setup_account(client)
    assert "Activer la double authentification" in client.get("/settings").text
    secret, _ = enable_mfa(client, clock)
    assert "Codes de secours restants : 10." in client.get("/settings").text
    log_out(client)

    r = log_in_password(client)
    assert r.headers["location"] == "/login/code"
    # the password alone opens nothing
    assert client.get("/budget", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/settings", follow_redirects=False).headers["location"] == "/login"
    # the code used to turn 2FA on cannot be replayed
    assert send_code(client, code_now(secret, clock)).status_code == 401
    clock.advance(30)
    r = send_code(client, code_now(secret, clock))
    assert r.status_code == 303 and r.headers["location"] == "/budget"
    assert client.get("/budget").status_code == 200


def test_recovery_code_works_once(client, clock):
    setup_account(client)
    _, codes = enable_mfa(client, clock)
    log_out(client)
    log_in_password(client)
    r = send_code(client, codes[0].upper().replace("-", " "))  # case and separators do not matter
    assert r.headers["location"] == "/budget"
    assert "Il t&#39;en reste 9" in client.get("/budget").text
    log_out(client)
    log_in_password(client)
    assert send_code(client, codes[0]).status_code == 401


def test_code_page_expires(client, clock):
    setup_account(client)
    secret, _ = enable_mfa(client, clock)
    log_out(client)
    log_in_password(client)
    clock.advance(301)
    token = csrf_of(client.get("/login").text)
    r = client.post("/login/code", data={"csrf": token, "code": code_now(secret, clock)}, follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert client.get("/budget", follow_redirects=False).headers["location"] == "/login"


def test_wrong_codes_and_passwords_share_the_throttle(client, clock):
    setup_account(client)
    secret, _ = enable_mfa(client, clock)
    log_out(client)
    log_in_password(client)
    for _ in range(4):
        assert send_code(client, "000000").status_code == 401
    # typing the right password again does not give more tries
    assert log_in_password(client).headers["location"] == "/login/code"
    assert send_code(client, "000000").status_code == 401
    clock.advance(30)
    assert send_code(client, code_now(secret, clock)).status_code == 429
    assert log_in_password(client).status_code == 429


def test_enable_requires_password_and_valid_code(client, clock):
    setup_account(client)
    client.post("/settings/totp/setup", data={"csrf": page_csrf(client)})
    secret = client.app.state.accounts.get_totp(1)["secret"]
    token = page_csrf(client, "/settings/totp")
    r = client.post("/settings/totp/enable", data={"csrf": token, "code": "123456", "password": PASSWORD})
    assert r.status_code == 400 and "Vérifie que ton téléphone est à l&#39;heure" in r.text
    r = client.post("/settings/totp/enable",
                    data={"csrf": token, "code": code_now(secret, clock), "password": "wrong password"})
    assert r.status_code == 400
    assert not client.app.state.accounts.totp_enabled(1)
    # cancelling the setup removes the pending secret
    client.post("/settings/totp/disable", data={"csrf": token})
    assert client.app.state.accounts.get_totp(1) is None


def test_other_browsers_are_logged_out_when_2fa_is_turned_on(app, client, clock):
    setup_account(client)
    with TestClient(app) as other:
        log_out(client)
        for c in (client, other):
            log_in_password(c)
        assert other.get("/budget").status_code == 200
        enable_mfa(client, clock)
        assert client.get("/budget").status_code == 200
        assert other.get("/budget", follow_redirects=False).headers["location"] == "/login"


def test_password_change_logs_out_other_browsers(app, client):
    setup_account(client)
    with TestClient(app) as other:
        log_in_password(other)
        new = "another password 42"
        r = client.post("/settings/password", data={"csrf": page_csrf(client), "current": PASSWORD,
                                                    "new": new, "new2": new}, follow_redirects=False)
        assert r.status_code == 303
        assert client.get("/budget").status_code == 200
        assert other.get("/budget", follow_redirects=False).headers["location"] == "/login"


def test_new_recovery_codes_replace_the_old_ones(client, clock):
    setup_account(client)
    secret, old = enable_mfa(client, clock)
    clock.advance(30)
    r = client.post("/settings/totp/codes", data={"csrf": page_csrf(client), "password": PASSWORD,
                                                  "code": code_now(secret, clock)})
    new = CODE_RE.findall(r.text)
    assert r.status_code == 200 and len(new) == 10 and not set(new) & set(old)
    log_out(client)
    log_in_password(client)
    assert send_code(client, old[1]).status_code == 401
    assert send_code(client, new[0]).status_code == 303


def test_disable_requires_password_and_code(client, clock):
    setup_account(client)
    secret, codes = enable_mfa(client, clock)
    store = client.app.state.accounts
    clock.advance(30)
    r = client.post("/settings/totp/disable", data={"csrf": page_csrf(client), "password": PASSWORD, "code": "000000"})
    assert r.status_code == 400 and store.totp_enabled(1)
    r = client.post("/settings/totp/disable", data={"csrf": page_csrf(client), "password": "nope nope nope",
                                                    "code": code_now(secret, clock)})
    assert r.status_code == 400 and store.totp_enabled(1)
    r = client.post("/settings/totp/disable", data={"csrf": page_csrf(client), "password": PASSWORD,
                                                    "code": codes[3]}, follow_redirects=False)
    assert r.status_code == 303 and not store.totp_enabled(1) and store.recovery_codes_left(1) == 0
    log_out(client)
    assert log_in_password(client).headers["location"] == "/budget"


def test_cli_disables_2fa(client, clock, tmp_path, monkeypatch, capsys):
    setup_account(client)
    enable_mfa(client, clock)
    monkeypatch.setenv("BUDGET_COCKPIT_DATA_DIR", str(tmp_path))
    assert cli.main(["disable-totp", "vincent"]) == 0
    assert "disabled" in capsys.readouterr().out
    assert not client.app.state.accounts.totp_enabled(1)
    # every browser is logged out, then the password alone works
    assert client.get("/budget", follow_redirects=False).headers["location"] == "/login"
    assert log_in_password(client).headers["location"] == "/budget"
    assert cli.main(["disable-totp", "nobody"]) == 1
