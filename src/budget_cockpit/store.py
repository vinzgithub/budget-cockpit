# Copyright © 2026 Vincent Fournet
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""SQLite storage, plus validated JSON import/export (backup format)."""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock

from .engine import FREQUENCIES, KINDS, Line, Override, Settings, parse_month

EXPORT_FORMAT = "budget-cockpit"
# Backups made before the project was renamed stay importable.
LEGACY_EXPORT_FORMATS = frozenset({"reste"})
EXPORT_VERSION = 1
MAX_AMOUNT = Decimal("1000000000")
MAX_LINES = 500
MAX_OVERRIDES = 20000

# One accounts database for the whole server...
ACCOUNTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
    pw_hash TEXT NOT NULL, created_at TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0, must_change_password INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL, last_used TEXT,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS totp (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    secret TEXT NOT NULL, last_step INTEGER NOT NULL DEFAULT 0, enabled_at TEXT);
CREATE TABLE IF NOT EXISTS recovery_codes (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL, used_at TEXT);
"""
ACCOUNTS_VERSION = "2"

# ...and one budget database per user.
BUDGET_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lines (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
    grp TEXT NOT NULL DEFAULT '', amount TEXT NOT NULL DEFAULT '0',
    frequency TEXT NOT NULL, month INTEGER, start TEXT, end_month TEXT,
    profile TEXT NOT NULL DEFAULT '[]', yearly_table TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1, position INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS overrides (
    line_id INTEGER NOT NULL REFERENCES lines(id) ON DELETE CASCADE,
    month TEXT NOT NULL, amount TEXT NOT NULL, paid INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (line_id, month));
"""
LINE_COLUMNS = ("id", "name", "kind", "grp", "amount", "frequency", "month", "start", "end_month",
                "profile", "yearly_table", "active", "position", "note")


class ValidationError(ValueError):
    """Invalid user data. ``key`` is a translation key, ``detail`` extra info."""

    def __init__(self, key: str, detail: str = ""):
        super().__init__(f"{key}: {detail}")
        self.key = key
        self.detail = detail


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------ parsing
def parse_amount(value, allow_empty: bool = False) -> Decimal | None:
    """Accepts 1234.56, 1234,56, 1 234,56, 1,234.56, '1 234,56 €', -59.90."""
    if value is None:
        if allow_empty:
            return None
        raise ValidationError("error.amount", "")
    if isinstance(value, int | Decimal):
        d = Decimal(value)
    else:
        s = str(value).strip()
        for ch in ("€", "$", "£", "CHF", " ", " ", " ", "'"):
            s = s.replace(ch, "")
        s = s.replace("−", "-")
        if s == "":
            if allow_empty:
                return None
            raise ValidationError("error.amount", str(value))
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
        try:
            d = Decimal(s)
        except InvalidOperation:
            raise ValidationError("error.amount", str(value)) from None
    if not d.is_finite() or abs(d) > MAX_AMOUNT:
        raise ValidationError("error.amount", str(value))
    return d


def parse_month_value(value, allow_empty: bool = True) -> str | None:
    if value is None or str(value).strip() == "":
        if allow_empty:
            return None
        raise ValidationError("error.month", "")
    try:
        y, m = parse_month(str(value))
    except (ValueError, AttributeError):
        raise ValidationError("error.month", str(value)) from None
    return f"{y:04d}-{m:02d}"


def clean_text(value, max_len: int, required: bool = False, field: str = "error.name") -> str:
    s = "" if value is None else str(value).strip()
    if required and not s:
        raise ValidationError(field, "")
    if len(s) > max_len:
        raise ValidationError("error.too_long", str(max_len))
    return s


def line_from_dict(data: dict, line_id: int = 0, position: int = 0) -> Line:
    """Builds and validates a Line from user/JSON data."""
    kind = str(data.get("kind", "expense"))
    if kind not in KINDS:
        raise ValidationError("error.kind", kind)
    freq = str(data.get("frequency", "monthly"))
    if freq not in FREQUENCIES:
        raise ValidationError("error.frequency", freq)
    month = data.get("month")
    if freq == "yearly":
        try:
            month = int(month)
        except (TypeError, ValueError):
            raise ValidationError("error.month_of_year", str(month)) from None
        if not 1 <= month <= 12:
            raise ValidationError("error.month_of_year", str(month))
    else:
        month = None
    profile_raw = data.get("profile") or []
    if freq == "profile":
        if len(profile_raw) != 12:
            raise ValidationError("error.profile", str(len(profile_raw)))
        profile = [parse_amount(v if str(v).strip() != "" else "0") for v in profile_raw]
    else:
        profile = [Decimal(0)] * 12
    table: dict[int, Decimal] = {}
    if freq == "yearly_table":
        for k, v in (data.get("yearly_table") or {}).items():
            try:
                year = int(k)
            except (TypeError, ValueError):
                raise ValidationError("error.year", str(k)) from None
            if not 1900 <= year <= 2200:
                raise ValidationError("error.year", str(k))
            table[year] = parse_amount(v)
        if not table:
            raise ValidationError("error.yearly_table", "")
    start = parse_month_value(data.get("start"))
    end = parse_month_value(data.get("end"))
    if start and end and end < start:
        raise ValidationError("error.end_before_start", f"{start} > {end}")
    amount = parse_amount(data.get("amount") if data.get("amount") not in (None, "") else "0")
    return Line(
        id=line_id,
        name=clean_text(data.get("name"), 80, required=True),
        kind=kind,
        group=clean_text(data.get("group"), 60),
        amount=amount,
        frequency=freq,
        month=month,
        start=start,
        end=end,
        profile=profile,
        yearly_table=table,
        active=bool(data.get("active", True)),
        position=position,
        note=clean_text(data.get("note"), 500),
    )


def settings_from_dict(data: dict) -> Settings:
    horizon = int(data.get("horizon", 24))
    if not 1 <= horizon <= 120:
        raise ValidationError("error.horizon", str(horizon))
    locale = str(data.get("locale", "fr"))
    if locale not in ("fr", "en"):
        raise ValidationError("error.locale", locale)
    currency = str(data.get("currency", "EUR"))
    if currency not in ("EUR", "USD", "GBP", "CHF"):
        raise ValidationError("error.currency", currency)
    return Settings(
        start_month=parse_month_value(data.get("start_month"), allow_empty=False),
        horizon=horizon,
        current_balance=parse_amount(data.get("current_balance", "0")),
        savings_balance=parse_amount(data.get("savings_balance", "0")),
        safety_threshold=parse_amount(data.get("safety_threshold", "100")),
        currency=currency,
        locale=locale,
    )


def settings_to_dict(s: Settings) -> dict:
    return {
        "start_month": s.start_month, "horizon": s.horizon,
        "current_balance": str(s.current_balance), "savings_balance": str(s.savings_balance),
        "safety_threshold": str(s.safety_threshold), "currency": s.currency, "locale": s.locale,
    }


def line_to_dict(line: Line) -> dict:
    return {
        "name": line.name, "kind": line.kind, "group": line.group, "amount": str(line.amount),
        "frequency": line.frequency, "month": line.month, "start": line.start, "end": line.end,
        "profile": [str(v) for v in line.profile] if line.frequency == "profile" else [],
        "yearly_table": {str(k): str(v) for k, v in sorted(line.yearly_table.items())},
        "active": line.active, "note": line.note,
    }


# ---------------------------------------------------------------- databases
class _Database:
    def __init__(self, path: Path, schema: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(schema)

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def _transaction(self):
        with self._lock:
            self.db.execute("BEGIN")
            try:
                yield
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            self.db.execute("COMMIT")

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self.db.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def _table_exists(self, name: str) -> bool:
        return self.db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                               (name,)).fetchone() is not None

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        columns = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


class Accounts(_Database):
    """Users, roles, sessions, two-factor authentication and API keys (budget_cockpit.db)."""

    def __init__(self, path: Path, budget_path: Callable[[int], Path]):
        super().__init__(path, ACCOUNTS_SCHEMA)
        self._ensure_column("users", "is_admin", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("users", "must_change_password", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("api_tokens", "user_id", "INTEGER REFERENCES users(id) ON DELETE CASCADE")
        if self.get_meta("schema_version") != ACCOUNTS_VERSION:
            self._migrate_single_user_database(budget_path)

    def _migrate_single_user_database(self, budget_path: Callable[[int], Path]) -> None:
        """Version 0.1 kept one budget in this file. It becomes the budget of the first
        account, which becomes the administrator. Safe to run again if interrupted."""
        first = self.db.execute("SELECT MIN(id) FROM users").fetchone()[0]
        legacy = self._table_exists("lines")
        if first is not None:
            with self._transaction():
                self.db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (first,))
                self.db.execute("UPDATE api_tokens SET user_id = ? WHERE user_id IS NULL", (first,))
            if legacy:
                self._copy_legacy_budget(budget_path(first))
        elif legacy and self.db.execute("SELECT COUNT(*) FROM lines").fetchone()[0]:
            return  # data without any account: leave it untouched
        with self._transaction():
            if legacy:
                self.db.execute("DROP TABLE IF EXISTS overrides")
                self.db.execute("DROP TABLE IF EXISTS lines")
                self.db.execute("DELETE FROM meta WHERE key = 'settings'")
            self.db.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (ACCOUNTS_VERSION,))

    def _copy_legacy_budget(self, target: Path) -> None:
        for suffix in ("", "-wal", "-shm"):  # leftovers of an interrupted migration
            Path(f"{target}{suffix}").unlink(missing_ok=True)
        columns = ", ".join(LINE_COLUMNS)  # constant names, not user input
        lines = self.db.execute(f"SELECT {columns} FROM lines").fetchall()  # noqa: S608
        overrides = self.db.execute("SELECT line_id, month, amount, paid FROM overrides").fetchall()
        settings = self.get_meta("settings")
        budget = Store(target)
        try:
            with budget._transaction():
                if settings is not None:
                    budget.db.execute("INSERT INTO meta(key, value) VALUES('settings', ?)", (settings,))
                budget.db.executemany(
                    f"INSERT INTO lines({columns}) VALUES({', '.join('?' * len(LINE_COLUMNS))})",  # noqa: S608
                    [tuple(r) for r in lines])
                budget.db.executemany("INSERT INTO overrides(line_id, month, amount, paid) VALUES(?, ?, ?, ?)",
                                      [tuple(r) for r in overrides])
            copied = (budget.db.execute("SELECT COUNT(*) FROM lines").fetchone()[0],
                      budget.db.execute("SELECT COUNT(*) FROM overrides").fetchone()[0])
        finally:
            budget.close()
        if copied != (len(lines), len(overrides)):
            raise RuntimeError(f"budget migration incomplete: {copied} != {(len(lines), len(overrides))}")

    def user_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    # users
    def create_user(self, username: str, pw_hash: str, is_admin: bool = False,
                    must_change_password: bool = False) -> int:
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO users(username, pw_hash, created_at, is_admin, must_change_password)"
                " VALUES(?, ?, ?, ?, ?)", (username, pw_hash, now(), int(is_admin), int(must_change_password)))
            return cur.lastrowid

    def list_users(self):
        return self.db.execute(
            "SELECT u.*, (t.enabled_at IS NOT NULL) AS totp_on FROM users u "
            "LEFT JOIN totp t ON t.user_id = u.id ORDER BY lower(u.username)").fetchall()

    def username_taken(self, username: str) -> bool:
        row = self.db.execute("SELECT 1 FROM users WHERE lower(username) = lower(?)", (username,)).fetchone()
        return row is not None

    def set_admin(self, user_id: int, is_admin: bool) -> None:
        with self._lock:
            self.db.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(is_admin), user_id))

    def delete_user(self, user_id: int) -> None:
        """Account, 2FA, recovery codes and API keys. The budget file is removed by Budgets.delete."""
        with self._transaction():
            self.db.execute("DELETE FROM api_tokens WHERE user_id = ?", (user_id,))
            self.db.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
            self.db.execute("DELETE FROM totp WHERE user_id = ?", (user_id,))
            self.db.execute("DELETE FROM meta WHERE key = ?", (f"session_epoch:{user_id}",))
            self.db.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def get_user_by_name(self, username: str):
        return self.db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    def get_user(self, user_id: int):
        return self.db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def set_password(self, user_id: int, pw_hash: str, must_change: bool = False) -> None:
        with self._lock:
            self.db.execute("UPDATE users SET pw_hash = ?, must_change_password = ? WHERE id = ?",
                            (pw_hash, int(must_change), user_id))

    # sessions: changing the epoch signs out every browser of that user
    def session_epoch(self, user_id: int) -> str:
        return self.get_meta(f"session_epoch:{user_id}") or "0"

    def new_session_epoch(self, user_id: int) -> str:
        epoch = secrets.token_hex(8)
        self.set_meta(f"session_epoch:{user_id}", epoch)
        return epoch

    # two-factor authentication (TOTP). A row without enabled_at is a setup in progress.
    def get_totp(self, user_id: int):
        return self.db.execute("SELECT * FROM totp WHERE user_id = ?", (user_id,)).fetchone()

    def totp_enabled(self, user_id: int) -> bool:
        row = self.get_totp(user_id)
        return bool(row and row["enabled_at"])

    def start_totp_setup(self, user_id: int, secret: str) -> None:
        with self._lock:
            self.db.execute("INSERT INTO totp(user_id, secret, last_step, enabled_at) VALUES(?, ?, 0, NULL) "
                            "ON CONFLICT(user_id) DO UPDATE SET secret = excluded.secret, last_step = 0, "
                            "enabled_at = NULL WHERE totp.enabled_at IS NULL", (user_id, secret))

    def enable_totp(self, user_id: int, step: int, code_hashes: list[str]) -> None:
        with self._transaction():
            self.db.execute("UPDATE totp SET enabled_at = ?, last_step = ? WHERE user_id = ?", (now(), step, user_id))
            self._replace_recovery_codes(user_id, code_hashes)

    def set_totp_last_step(self, user_id: int, step: int) -> None:
        with self._lock:
            self.db.execute("UPDATE totp SET last_step = MAX(last_step, ?) WHERE user_id = ?", (step, user_id))

    def disable_totp(self, user_id: int) -> None:
        with self._transaction():
            self.db.execute("DELETE FROM totp WHERE user_id = ?", (user_id,))
            self.db.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))

    def _replace_recovery_codes(self, user_id: int, code_hashes: list[str]) -> None:
        self.db.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
        self.db.executemany("INSERT INTO recovery_codes(user_id, code_hash) VALUES(?, ?)",
                            [(user_id, h) for h in code_hashes])

    def replace_recovery_codes(self, user_id: int, code_hashes: list[str]) -> None:
        with self._transaction():
            self._replace_recovery_codes(user_id, code_hashes)

    def use_recovery_code(self, user_id: int, code_hash: str) -> bool:
        with self._lock:
            cur = self.db.execute("UPDATE recovery_codes SET used_at = ? WHERE id = (SELECT id FROM recovery_codes "
                                  "WHERE user_id = ? AND code_hash = ? AND used_at IS NULL LIMIT 1)",
                                  (now(), user_id, code_hash))
            return cur.rowcount == 1

    def recovery_codes_left(self, user_id: int) -> int:
        return self.db.execute("SELECT COUNT(*) FROM recovery_codes WHERE user_id = ? AND used_at IS NULL",
                               (user_id,)).fetchone()[0]


    # API keys (each belongs to one user and reads that user's budget)
    def add_token(self, user_id: int, name: str, token_hash: str) -> None:
        with self._lock:
            self.db.execute("INSERT INTO api_tokens(name, token_hash, created_at, user_id) VALUES(?, ?, ?, ?)",
                            (name, token_hash, now(), user_id))

    def list_tokens(self, user_id: int):
        return self.db.execute("SELECT id, name, created_at, last_used FROM api_tokens WHERE user_id = ? ORDER BY id",
                               (user_id,)).fetchall()

    def check_token(self, token_hash: str) -> int | None:
        """The id of the user the key belongs to, or None."""
        with self._lock:
            row = self.db.execute("SELECT id, user_id FROM api_tokens WHERE token_hash = ? AND user_id IS NOT NULL",
                                  (token_hash,)).fetchone()
            if row:
                self.db.execute("UPDATE api_tokens SET last_used = ? WHERE id = ?", (now(), row["id"]))
            return row["user_id"] if row else None

    def revoke_token(self, user_id: int, token_id: int) -> None:
        with self._lock:
            self.db.execute("DELETE FROM api_tokens WHERE id = ? AND user_id = ?", (token_id, user_id))


class Store(_Database):
    """The budget of one user: settings, lines and per-month amounts (budgets/user-<id>.db)."""

    def __init__(self, path: Path):
        super().__init__(path, BUDGET_SCHEMA)

    def get_settings(self, default_month: str, default_locale: str = "fr") -> Settings:
        raw = self.get_meta("settings")
        if raw is None:
            return Settings(start_month=default_month, locale=default_locale)
        return settings_from_dict(json.loads(raw))

    def save_settings(self, s: Settings) -> None:
        self.set_meta("settings", json.dumps(settings_to_dict(s)))

    # lines
    @staticmethod
    def _row_to_line(r) -> Line:
        profile = [Decimal(v) for v in json.loads(r["profile"])] or [Decimal(0)] * 12
        table = {int(k): Decimal(v) for k, v in json.loads(r["yearly_table"]).items()}
        return Line(id=r["id"], name=r["name"], kind=r["kind"], group=r["grp"],
                    amount=Decimal(r["amount"]), frequency=r["frequency"], month=r["month"],
                    start=r["start"], end=r["end_month"], profile=profile, yearly_table=table,
                    active=bool(r["active"]), position=r["position"], note=r["note"])

    def list_lines(self) -> list[Line]:
        rows = self.db.execute("SELECT * FROM lines ORDER BY position, id").fetchall()
        return [self._row_to_line(r) for r in rows]

    def get_line(self, line_id: int) -> Line | None:
        r = self.db.execute("SELECT * FROM lines WHERE id = ?", (line_id,)).fetchone()
        return self._row_to_line(r) if r else None

    def _line_params(self, line: Line) -> tuple:
        return (line.name, line.kind, line.group, str(line.amount), line.frequency, line.month,
                line.start, line.end,
                json.dumps([str(v) for v in line.profile] if line.frequency == "profile" else []),
                json.dumps({str(k): str(v) for k, v in line.yearly_table.items()}),
                int(line.active), line.position, line.note)

    def save_line(self, line: Line) -> int:
        with self._lock:
            if line.id:
                self.db.execute(
                    "UPDATE lines SET name=?, kind=?, grp=?, amount=?, frequency=?, month=?, start=?,"
                    " end_month=?, profile=?, yearly_table=?, active=?, position=?, note=? WHERE id=?",
                    (*self._line_params(line), line.id))
                return line.id
            if self.db.execute("SELECT COUNT(*) FROM lines").fetchone()[0] >= MAX_LINES:
                raise ValidationError("error.too_many_lines", str(MAX_LINES))
            line.position = (self.db.execute("SELECT COALESCE(MAX(position), 0) FROM lines")
                             .fetchone()[0] + 1)
            cur = self.db.execute(
                "INSERT INTO lines(name, kind, grp, amount, frequency, month, start, end_month,"
                " profile, yearly_table, active, position, note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                self._line_params(line))
            return cur.lastrowid

    def delete_line(self, line_id: int) -> None:
        with self._lock:
            self.db.execute("DELETE FROM lines WHERE id = ?", (line_id,))

    def move_line(self, line_id: int, direction: int) -> None:
        """Swap with the previous/next line of the same kind."""
        with self._lock:
            lines = [ln for ln in self.list_lines()]
            target = next((ln for ln in lines if ln.id == line_id), None)
            if target is None:
                return
            same = [ln for ln in lines if ln.kind == target.kind]
            i = same.index(target)
            j = i + direction
            if not 0 <= j < len(same):
                return
            other = same[j]
            self.db.execute("BEGIN")
            self.db.execute("UPDATE lines SET position = ? WHERE id = ?", (other.position, target.id))
            self.db.execute("UPDATE lines SET position = ? WHERE id = ?", (target.position, other.id))
            if other.position == target.position:
                self.db.execute("UPDATE lines SET position = position + ? WHERE id = ?",
                                (direction, target.id))
            self.db.execute("COMMIT")

    # overrides
    def list_overrides(self) -> list[Override]:
        rows = self.db.execute("SELECT * FROM overrides").fetchall()
        return [Override(r["line_id"], r["month"], Decimal(r["amount"]), bool(r["paid"])) for r in rows]

    def get_override(self, line_id: int, month: str) -> Override | None:
        r = self.db.execute("SELECT * FROM overrides WHERE line_id = ? AND month = ?",
                            (line_id, month)).fetchone()
        return Override(r["line_id"], r["month"], Decimal(r["amount"]), bool(r["paid"])) if r else None

    def set_override(self, line_id: int, month: str, amount: Decimal, paid: bool = False) -> None:
        with self._lock:
            n = self.db.execute("SELECT COUNT(*) FROM overrides").fetchone()[0]
            if n >= MAX_OVERRIDES and self.get_override(line_id, month) is None:
                raise ValidationError("error.too_many_overrides", str(MAX_OVERRIDES))
            self.db.execute(
                "INSERT INTO overrides(line_id, month, amount, paid) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(line_id, month) DO UPDATE SET amount = excluded.amount, paid = excluded.paid",
                (line_id, month, str(amount), int(paid)))

    def delete_override(self, line_id: int, month: str) -> None:
        with self._lock:
            self.db.execute("DELETE FROM overrides WHERE line_id = ? AND month = ?", (line_id, month))

    def replace_overrides(self, overrides: list[Override]) -> None:
        with self._lock:
            self.db.execute("BEGIN")
            self.db.execute("DELETE FROM overrides")
            self.db.executemany(
                "INSERT INTO overrides(line_id, month, amount, paid) VALUES(?, ?, ?, ?)",
                [(o.line_id, o.month, str(o.amount), int(o.paid)) for o in overrides])
            self.db.execute("COMMIT")

    # backup
    def export_data(self, settings: Settings) -> dict:
        by_line: dict[int, list[dict]] = {}
        for o in self.list_overrides():
            by_line.setdefault(o.line_id, []).append(
                {"month": o.month, "amount": str(o.amount), "paid": o.paid})
        lines = []
        for line in self.list_lines():
            d = line_to_dict(line)
            d["overrides"] = sorted(by_line.get(line.id, []), key=lambda x: x["month"])
            lines.append(d)
        return {"format": EXPORT_FORMAT, "version": EXPORT_VERSION, "exported_at": now(),
                "settings": settings_to_dict(settings), "lines": lines}

    def import_data(self, data: dict) -> Settings:
        """Validates everything first, then replaces settings, lines and exceptions."""
        if not isinstance(data, dict) or (
            data.get("format") != EXPORT_FORMAT and data.get("format") not in LEGACY_EXPORT_FORMATS
        ):
            raise ValidationError("error.import_format", "")
        if data.get("version") != EXPORT_VERSION:
            raise ValidationError("error.import_version", str(data.get("version")))
        settings = settings_from_dict(data.get("settings") or {})
        raw_lines = data.get("lines") or []
        if not isinstance(raw_lines, list) or len(raw_lines) > MAX_LINES:
            raise ValidationError("error.too_many_lines", str(MAX_LINES))
        parsed: list[tuple[Line, list[Override]]] = []
        total_overrides = 0
        for pos, raw in enumerate(raw_lines, start=1):
            line = line_from_dict(raw, 0, pos)
            ovs = []
            for o in raw.get("overrides") or []:
                ovs.append(Override(0, parse_month_value(o.get("month"), allow_empty=False),
                                    parse_amount(o.get("amount", "0")), bool(o.get("paid", False))))
            total_overrides += len(ovs)
            parsed.append((line, ovs))
        if total_overrides > MAX_OVERRIDES:
            raise ValidationError("error.too_many_overrides", str(MAX_OVERRIDES))
        with self._lock:
            self.db.execute("BEGIN")
            try:
                self.db.execute("DELETE FROM overrides")
                self.db.execute("DELETE FROM lines")
                for line, ovs in parsed:
                    cur = self.db.execute(
                        "INSERT INTO lines(name, kind, grp, amount, frequency, month, start, end_month,"
                        " profile, yearly_table, active, position, note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        self._line_params(line))
                    for o in ovs:
                        self.db.execute(
                            "INSERT OR REPLACE INTO overrides(line_id, month, amount, paid) VALUES(?,?,?,?)",
                            (cur.lastrowid, o.month, str(o.amount), int(o.paid)))
                self.db.execute(
                    "INSERT INTO meta(key, value) VALUES('settings', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (json.dumps(settings_to_dict(settings)),))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        return settings


class Budgets:
    """Opens each user's budget database on first use and keeps it open."""

    def __init__(self, path_for: Callable[[int], Path]):
        self._path_for = path_for
        self._stores: dict[int, Store] = {}
        self._lock = RLock()

    def get(self, user_id: int) -> Store:
        with self._lock:
            store = self._stores.get(user_id)
            if store is None:
                store = self._stores[user_id] = Store(self._path_for(user_id))
            return store

    def delete(self, user_id: int) -> None:
        with self._lock:
            store = self._stores.pop(user_id, None)
            if store is not None:
                store.close()
            path = self._path_for(user_id)
            for suffix in ("", "-wal", "-shm"):
                Path(f"{path}{suffix}").unlink(missing_ok=True)

    def close(self) -> None:
        with self._lock:
            for store in self._stores.values():
                store.close()
            self._stores.clear()
