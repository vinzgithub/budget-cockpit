# Installation

🇬🇧 [English version](INSTALLATION.md)

Budget Cockpit est un seul petit conteneur (environ 60 Mo de RAM). Il garde tout dans un fichier SQLite sous `/data`.

> **Garde-le privé.** Budget Cockpit contient tes données financières. Fais-le tourner sur ton réseau local ou derrière un VPN (WireGuard, Tailscale…). Ne l'expose pas directement sur Internet.

## Docker Compose (recommandé)

```bash
mkdir budget-cockpit && cd budget-cockpit
curl -fsSLO https://raw.githubusercontent.com/vinzgithub/budget-cockpit/main/docker-compose.yml
docker compose up -d
```

Ouvre `http://<ton-serveur>:8080`. La première visite te demande de créer le seul compte : il n'y a pas de mot de passe par défaut.

Le [`docker-compose.yml`](../../docker-compose.yml) fourni lance déjà Budget Cockpit de façon durcie : utilisateur non root, système de fichiers en lecture seule, aucune capacité Linux, `no-new-privileges`.

### Réglages (variables d'environnement)

| Variable | Par défaut | Rôle |
|---|---|---|
| `BUDGET_COCKPIT_DATA_DIR` | `/data` | Emplacement de la base et de la clé de session |
| `BUDGET_COCKPIT_COOKIE_SECURE` | `false` | À mettre à `true` quand Budget Cockpit est servi en HTTPS |
| `BUDGET_COCKPIT_FRAME_ANCESTORS` | `'self'` | Origines autorisées à afficher Budget Cockpit dans un cadre (Home Assistant par exemple) |
| `BUDGET_COCKPIT_DEFAULT_LOCALE` | `fr` | Langue avant la création du compte (`fr` ou `en`) |
| `BUDGET_COCKPIT_SESSION_MAX_AGE` | `604800` | Durée de session en secondes (7 jours) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | IP de ton reverse proxy, pour que Budget Cockpit voie la vraie adresse des clients |
| `TZ` | — | Ton fuseau horaire, par exemple `Europe/Paris` |

## Proxmox : un petit conteneur LXC

Pas besoin d'une VM complète. Un LXC Debian suffit :

1. Dans Proxmox, crée un conteneur **non privilégié** à partir d'un modèle Debian (12 ou 13) : 1 vCPU, 512 Mo de RAM, 4 Go de disque.
2. Dans **Options → Fonctionnalités**, active **nesting** et **keyctl** (nécessaires pour Docker dans un LXC).
3. Dans le conteneur, installe Docker Engine et son extension Compose en suivant les [instructions officielles pour Debian](https://docs.docker.com/engine/install/debian/).
4. Puis suis les étapes Docker Compose ci-dessus.
5. Ajoute le conteneur à ta tâche de sauvegarde Proxmox.

## Derrière un reverse proxy (HTTPS)

Fais pointer ton proxy (Caddy, Nginx Proxy Manager, Traefik…) vers `http://<conteneur>:8080`, puis règle :

```yaml
environment:
  BUDGET_COCKPIT_COOKIE_SECURE: "true"
  FORWARDED_ALLOW_IPS: "<ip du proxy>"
```

## Sans Docker

```bash
python3 -m venv /opt/budget-cockpit
/opt/budget-cockpit/bin/pip install "git+https://github.com/vinzgithub/budget-cockpit"
BUDGET_COCKPIT_DATA_DIR=/var/lib/budget-cockpit /opt/budget-cockpit/bin/uvicorn --factory budget_cockpit.app:create_app --host 0.0.0.0 --port 8080
```

## Sauvegardes

- **Depuis l'interface** : Paramètres → Sauvegarde → Exporter (JSON). L'import restaure tout.
- **Le volume** : sauvegarde le volume `budget-cockpit-data` (`budget_cockpit.db`, plus `budget_cockpit.db-wal` pendant le fonctionnement).

## Mise à jour

```bash
docker compose pull && docker compose up -d
```

## Mot de passe oublié ?

```bash
docker compose exec budget-cockpit python -m budget_cockpit.cli reset-password <identifiant>
```
