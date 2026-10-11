# Security

🇫🇷 Les signalements en français sont bienvenus.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on this repository. You will get an answer within a few days.

## Supported versions

Only the latest release receives security fixes.

## Design

Budget Cockpit holds personal financial data, so it is built to be safe by default:

- **No default credentials.** The first visit creates the single account. Passwords are hashed with scrypt (N=2^14, r=8, p=1).
- **Login throttling.** Five failed attempts from one address lock it out for five minutes.
- **Sessions.** Signed cookies (`HttpOnly`, `SameSite=Lax`, `Secure` when `BUDGET_COCKPIT_COOKIE_SECURE=true`). The signing key is generated on first start and stored in the data directory with mode `0600`.
- **CSRF.** Every state-changing request requires a per-session token.
- **Content-Security-Policy.** `default-src 'self'`, no inline scripts or styles, no external resources, `frame-ancestors` restricted. Budget Cockpit makes no outbound network calls.
- **API keys.** Read-only, shown once, stored only as SHA-256 hashes, revocable.
- **Input validation.** All amounts, months and imports are validated server-side, with size limits.
- **Container.** Runs as an unprivileged user (UID 10001); the provided Compose file adds a read-only root filesystem, `cap_drop: ALL` and `no-new-privileges`.

## Deployment recommendations

- Keep Budget Cockpit on your local network or behind a VPN. Do not expose it directly to the Internet.
- Serve it over HTTPS when it leaves your machine, and set `BUDGET_COCKPIT_COOKIE_SECURE=true`.
- Back up the `/data` volume: it contains your data and the session key.
