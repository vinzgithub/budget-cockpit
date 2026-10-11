# Installation

🇫🇷 [Version française](INSTALLATION.fr.md)

Budget Cockpit is a single small container (about 60 MB of RAM). It stores everything in one SQLite file under `/data`.

> **Keep it private.** Budget Cockpit holds your financial data. Run it on your local network or behind a VPN (WireGuard, Tailscale…). Do not expose it directly to the Internet.

## Docker Compose (recommended)

```bash
mkdir budget-cockpit && cd budget-cockpit
curl -fsSLO https://raw.githubusercontent.com/vinzgithub/budget-cockpit/main/docker-compose.yml
docker compose up -d
```

Open `http://<your-server>:8080`. The first visit asks you to create the only account — there is no default password.

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
- **The volume**: back up the `budget-cockpit-data` volume (`budget_cockpit.db`, plus `budget_cockpit.db-wal` while running).

## Updating

```bash
docker compose pull && docker compose up -d
```

## Forgot your password?

```bash
docker compose exec budget-cockpit python -m budget_cockpit.cli reset-password <username>
```
