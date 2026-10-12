# Copyright © 2026 Vincent Fournet
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    data_dir: Path
    cookie_secure: bool = False
    frame_ancestors: str = "'self'"
    default_locale: str = "fr"
    session_max_age: int = 7 * 24 * 3600

    @property
    def db_path(self) -> Path:
        """Accounts: users, roles, two-factor authentication, API keys."""
        return self.data_dir / "budget_cockpit.db"

    def budget_path(self, user_id: int) -> Path:
        """One database per user for their budget."""
        return self.data_dir / "budgets" / f"user-{int(user_id)}.db"

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            data_dir=Path(os.environ.get("BUDGET_COCKPIT_DATA_DIR", "./data")),
            cookie_secure=_bool(os.environ.get("BUDGET_COCKPIT_COOKIE_SECURE"), False),
            frame_ancestors=os.environ.get("BUDGET_COCKPIT_FRAME_ANCESTORS", "'self'"),
            default_locale=os.environ.get("BUDGET_COCKPIT_DEFAULT_LOCALE", "fr"),
            session_max_age=int(os.environ.get("BUDGET_COCKPIT_SESSION_MAX_AGE", str(7 * 24 * 3600))),
        )


def secret_key(data_dir: Path) -> str:
    """Session signing key, generated on first start and kept in the data dir."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / ".secret_key"
    if path.exists():
        return path.read_text().strip()
    key = secrets.token_urlsafe(48)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    return key
