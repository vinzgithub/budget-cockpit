"""Command line helpers.

    python -m budget_cockpit.cli reset-password <username>
"""

from __future__ import annotations

import argparse
import getpass
import sys

from .config import Config
from .security import MIN_PASSWORD_LENGTH, hash_password
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m budget_cockpit.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    reset = sub.add_parser("reset-password", help="set a new password for an account")
    reset.add_argument("username")
    args = parser.parse_args(argv)

    store = Store(Config.from_env().db_path)
    if args.command == "reset-password":
        user = store.get_user_by_name(args.username)
        if user is None:
            print(f"Unknown user: {args.username}", file=sys.stderr)
            return 1
        password = getpass.getpass("New password: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"Password too short (at least {MIN_PASSWORD_LENGTH} characters).", file=sys.stderr)
            return 1
        if password != getpass.getpass("Confirm: "):
            print("Passwords do not match.", file=sys.stderr)
            return 1
        store.set_password(user["id"], hash_password(password))
        print("Password updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
