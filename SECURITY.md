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
- **Login throttling.** Five failed attempts from one address lock it out for five minutes. Wrong passwords and wrong two-factor codes count together, and so do password checks in Settings.
- **Two-factor authentication (optional).** TOTP (RFC 6238: SHA-1, 6 digits, 30 s) with one step of tolerance for clock drift; each code is accepted once. The QR code is generated on the server. Ten single-use recovery codes are shown once and stored as SHA-256 hashes. Turning it on or off, or renewing the codes, requires the password and a valid code. The TOTP secret itself has to stay readable to check codes: it is stored in the database, so protect the `/data` volume and its backups. If the phone and the recovery codes are lost, whoever can run commands on the server can turn it off: `python -m budget_cockpit.cli disable-totp <username>`.
- **Sessions.** Signed cookies (`HttpOnly`, `SameSite=Lax`, `Secure` when `BUDGET_COCKPIT_COOKIE_SECURE=true`). The signing key is generated on first start and stored in the data directory with mode `0600`. Changing the password or the two-factor settings logs out every other browser.
- **CSRF.** Every state-changing request requires a per-session token.
- **Content-Security-Policy.** `default-src 'self'`, no inline scripts or styles, no external resources, `frame-ancestors` restricted. Budget Cockpit makes no outbound network calls.
- **API keys.** Read-only, shown once, stored only as SHA-256 hashes, revocable.
- **Input validation.** All amounts, months and imports are validated server-side, with size limits.
- **Container.** Runs as an unprivileged user (UID 10001); the provided Compose file adds a read-only root filesystem, `cap_drop: ALL` and `no-new-privileges`.

## Deployment recommendations

- Keep Budget Cockpit on your local network or behind a VPN. Do not expose it directly to the Internet.
- Serve it over HTTPS when it leaves your machine, and set `BUDGET_COCKPIT_COOKIE_SECURE=true`.
- Back up the `/data` volume: it contains your data and the session key.
