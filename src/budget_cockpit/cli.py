# Copyright © 2026 Vincent Fournet
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Command line helpers, for the person who runs the server.

    python -m budget_cockpit.cli reset-password <username>
    python -m budget_cockpit.cli disable-totp <username>
    python -m budget_cockpit.cli make-admin <username>
"""

from __future__ import annotations

import argparse
import getpass
import sys

from .config import Config
from .security import MIN_PASSWORD_LENGTH, hash_password
from .store import Accounts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m budget_cockpit.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    reset = sub.add_parser("reset-password", help="set a new password for an account")
    reset.add_argument("username")
    totp = sub.add_parser("disable-totp", help="turn off two-factor authentication (lost phone and recovery codes)")
    totp.add_argument("username")
    admin = sub.add_parser("make-admin", help="give an account the administrator role")
    admin.add_argument("username")
    args = parser.parse_args(argv)

    config = Config.from_env()
    store = Accounts(config.db_path, config.budget_path)
    user = store.get_user_by_name(args.username)
    if user is None:
        print(f"Unknown user: {args.username}", file=sys.stderr)
        return 1

    if args.command == "reset-password":
        password = getpass.getpass("New password: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"Password too short (at least {MIN_PASSWORD_LENGTH} characters).", file=sys.stderr)
            return 1
        if password != getpass.getpass("Confirm: "):
            print("Passwords do not match.", file=sys.stderr)
            return 1
        store.set_password(user["id"], hash_password(password))
        store.new_session_epoch(user["id"])
        print("Password updated. Every browser has been signed out.")
    elif args.command == "disable-totp":
        if store.get_totp(user["id"]) is None:
            print("Two-factor authentication is not enabled for this account.")
            return 0
        store.disable_totp(user["id"])
        store.new_session_epoch(user["id"])
        print("Two-factor authentication disabled. Every browser has been signed out.")
        print("Sign in with the password only, then turn it on again in Settings.")
    elif args.command == "make-admin":
        store.set_admin(user["id"], True)
        print(f"{args.username} is now an administrator.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
