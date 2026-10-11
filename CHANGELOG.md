# Changelog

## 0.2.0 — 2026-10-11

- **Several accounts, each with its own budget.** The first account is the administrator. In the new **Admin** page (password asked again, then open for ten minutes), administrators create accounts with a temporary password, reset passwords, turn off a user's two-factor authentication, change roles and delete accounts. They never see anyone else's budget. A new account must choose its own password first.
- Storage: `budget_cockpit.db` now holds the accounts, two-factor authentication and API keys; each person's budget is in `budgets/user-<id>.db`. On first start, the 0.1 budget moves into the first account's database, which becomes the administrator and keeps its API keys. **Export a JSON backup before updating.**
- API keys read the budget of the account that created them. Backups are exported per person.
- CLI: `make-admin <username>`.
- **Two-factor authentication (optional).** In Settings, scan a QR code with an authenticator app: after the password, Budget Cockpit then asks for its 6-digit code. Ten single-use recovery codes are given for a lost phone, and `python -m budget_cockpit.cli disable-totp <username>` turns it off from the server as a last resort.
- Changing the password, or turning two-factor authentication on or off, logs out every other browser.
- Password checks in Settings are now throttled like the login page.
- The savings balance can be updated on the budget page, next to the account balance. In Settings, "My situation" now comes before the month closing, whose own savings field was easy to mistake for it.
- New dependency: `segno` (BSD-3-Clause) draws the QR code on the server, so nothing is loaded from the Internet.

## 0.1.1 — 2026-10-11

- The projected total savings now sit right under the savings lines in the budget grid (they used to be at the very bottom), so each planned transfer shows its effect immediately. A negative amount on a savings line is a withdrawal back to the current account.

## 0.1.0 — 2026-10-11

First public version.

- Month-by-month forecast of the current-account balance ("Left at end of month"), from the real balance of today, which can be updated directly on the budget page.
- Budget lines with rules: every month, once a year, different amount each month, manual entry, annual amount per year spread over twelve months; optional start and end months; groups.
- Per-month exceptions typed directly in the grid; "paid / received" ticks for the current month.
- Safety threshold with *Warning* and *Overdraft* alerts; tightest month.
- Month closing: the past month disappears and the real balances become the new starting point.
- Yearly summary with real saving capacity and group results.
- Read-only JSON API with revocable keys, documented for Home Assistant.
- JSON export / import.
- French and English interface and documentation.
- Docker image (non-root), hardened Compose file, CI and release workflows.
