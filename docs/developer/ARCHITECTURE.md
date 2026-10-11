# Architecture

🇫🇷 [Version française](ARCHITECTURE.fr.md)

Budget Cockpit is deliberately small: a server-rendered FastAPI app, SQLite, and a few hundred lines of vanilla JavaScript. No build step, no CDN, no external calls.

```
src/budget_cockpit/
├── engine.py     pure projection engine (no I/O) — the heart of Budget Cockpit
├── store.py      SQLite storage, validation, JSON import/export
├── app.py        FastAPI routes, sessions, CSRF, API
├── security.py   scrypt hashing, tokens, CSRF helpers, login throttle
├── i18n.py       fr/en catalogues, money and month formatting
├── config.py     environment variables, session key
├── cli.py        reset-password
├── locales/      fr.json, en.json
├── templates/    Jinja2 pages
├── static/       app.css, app.js, favicon.svg
└── data/         demo data (fictional) in fr and en
```

## The model

- **Settings** — first month shown (`start_month`), horizon, current-account balance today, savings today, safety threshold, currency, language.
- **Line** — `kind` (income · expense · savings), optional `group`, and a rule:
  - `monthly`: `amount` every month
  - `yearly`: `amount` in month `month`
  - `profile`: twelve amounts, one per calendar month
  - `manual`: zero unless a value is typed
  - `yearly_table`: `{year: annual amount}`, spread as amount / 12; the last year given carries forward
  - optional `start` / `end` months (inclusive), `active` flag
- **Override** — `(line, month) → amount`, typed in the grid. `paid = true` means the movement already happened: it is in today's balance, so it counts as zero.

## The projection

For each month *m* of the horizon:

```
cell(line, m)  = override if any, else rule(line, m)
balance(m)     = balance(m − 1) + income(m) − expenses(m) − savings(m)    (balance(−1) = today's balance)
alert(m)       = overdraft if balance(m) < 0, warning if balance(m) < threshold
```

All amounts are `Decimal` and are never rounded during computation, only for display. **Closing a month** moves `start_month` forward, drops the closed month's overrides and stores the real balances as the new starting point.

## Security

See [SECURITY.md](../../SECURITY.md). In short: no default credentials, scrypt, signed `HttpOnly` cookies, CSRF tokens on every POST, strict CSP without inline code, hashed read-only API keys, non-root container.

## Tests

```bash
python -m pip install -e ".[dev]"
ruff check src tests
python -m pytest
```

The engine tests use fictional data only. Never commit real financial data: `data/` and backups are git-ignored.
