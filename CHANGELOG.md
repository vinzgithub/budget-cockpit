# Changelog

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
