# Installation

🇫🇷 [Version française](INSTALLATION.fr.md)

Budget Cockpit is a single small container (about 60 MB of RAM). It stores everything in SQLite files under `/data`: `budget_cockpit.db` for the accounts, and `budgets/user-<id>.db` for each person's budget.

> **Keep it private.** Budget Cockpit holds your financial data. Run it on your local network or behind a VPN (WireGuard, Tailscale…). Do not expose it directly to the Internet.

## Docker Compose (recommended)

```bash
mkdir budget-cockpit && cd budget-cockpit
curl -fsSLO https://raw.githubusercontent.com/vinzgithub/budget-cockpit/main/docker-compose.yml
docker compose up -d
```

Open `http://<your-server>:8080`. The first visit asks you to create the administrator account — there is no default password. Create the other accounts in **Admin**: each gets its own budget.

The provided [`docker-compose.yml`](../../docker-compose.yml) already runs Budget Cockpit hardened: non-root user, read-only filesystem, no Linux capabilities, `no-new-privileges`.

### Settings (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `BUDGET_COCKPIT_DATA_DIR` | `/data` | Where the database and the session key live |
| `BUDGET_COCKPIT_COOKIE_SECURE` | `false` | Set to `true` when Budget Cockpit is served over HTTPS |
| `BUDGET_COCKPIT_FRAME_ANCESTORS` | `'self'` | Origins allowed to show Budget Cockpit in a frame (e.g. Home Assistant) |
| `BUDGET_COCKPIT_DEFAULT_LOCALE` | `fr` | Language before the first account exists (`fr` or `en`) |
| `BUDGET_COCKPIT_SESSION_MAX_AGE` | `604800` | Session length in seconds (7 days) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | IP of your reverse proxy, so Budget Cockpit sees real client addresses |
| `TZ` | — | Your time zone, e.g. `Europe/Paris` |

## Proxmox: a small LXC container

A full VM is not needed. A Debian LXC is enough:

1. In Proxmox, create an **unprivileged** container from a Debian template (12 or 13): 1 vCPU, 512 MB RAM, 4 GB disk.
2. In **Options → Features**, enable **nesting** and **keyctl** (required for Docker in an LXC).
3. Inside the container, install Docker Engine and its Compose plugin by following the [official Debian instructions](https://docs.docker.com/engine/install/debian/).
4. Then follow the Docker Compose steps above.
5. Include the container in your Proxmox backup job.

## Behind a reverse proxy (HTTPS)

Point your proxy (Caddy, Nginx Proxy Manager, Traefik…) to `http://<container>:8080`, then set:

```yaml
environment:
  BUDGET_COCKPIT_COOKIE_SECURE: "true"
  FORWARDED_ALLOW_IPS: "<ip of the proxy>"
```

## Without Docker

```bash
python3 -m venv /opt/budget-cockpit
/opt/budget-cockpit/bin/pip install "git+https://github.com/vinzgithub/budget-cockpit"
BUDGET_COCKPIT_DATA_DIR=/var/lib/budget-cockpit /opt/budget-cockpit/bin/uvicorn --factory budget_cockpit.app:create_app --host 0.0.0.0 --port 8080
```

## Backups

- **From the interface**: Settings → Backup → Export (JSON). Import restores everything.
- **From the interface**, each person exports their own budget; the administrator cannot export someone else's.
- **The volume**: back up the whole `budget-cockpit-data` volume (`budget_cockpit.db` and the `budgets/` folder, plus the `-wal` files while running).

## Updating

```bash
docker compose pull && docker compose up -d
```

**From 0.1 to 0.2**: on first start, the budget of version 0.1 moves into the first account's own database, and that account becomes the administrator. Export a JSON backup before updating, just in case.

## Forgot your password?

```bash
docker compose exec budget-cockpit python -m budget_cockpit.cli reset-password <username>
```

## Two-factor authentication

Turn it on in **Settings → Two-factor authentication**: scan the QR code with an authenticator app (Google Authenticator, Microsoft Authenticator, Aegis, 2FAS, Bitwarden…), type the first code and your password, then save the ten recovery codes somewhere other than that phone.

The codes depend on the time: the server clock must be right. In a Proxmox container it follows the host, so check that the host keeps its time in sync (it does by default).

Lost the phone **and** the recovery codes? Turn it off from the server, then log in with the password only:

```bash
docker compose exec budget-cockpit python -m budget_cockpit.cli disable-totp <username>
```
