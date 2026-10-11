# Architecture

🇬🇧 [English version](ARCHITECTURE.md)

Budget Cockpit est volontairement petit : une application FastAPI rendue côté serveur, SQLite, et quelques centaines de lignes de JavaScript sans framework. Pas d'étape de compilation, pas de CDN, aucun appel externe.

```
src/budget_cockpit/
├── engine.py     moteur de projection pur (sans entrées/sorties) — le cœur de Budget Cockpit
├── store.py      stockage SQLite (comptes, un budget par utilisateur), validation, import/export JSON
├── app.py        routes FastAPI, sessions, CSRF, API
├── security.py   hachage scrypt, TOTP et codes de secours, clés, CSRF, limitation des connexions
├── charts.py     courbes SVG dessinées par le serveur (sans bibliothèque JavaScript)
├── i18n.py       catalogues fr/en, formatage des montants et des mois
├── config.py     variables d'environnement, clé de session
├── cli.py        mot de passe, double authentification, rôle administrateur
├── locales/      fr.json, en.json
├── templates/    pages Jinja2
├── static/       app.css, app.js, favicon.svg
└── data/         données d'exemple (fictives) en fr et en
```

## Stockage

```
/data/
├── budget_cockpit.db      comptes, rôles, double authentification, clés d'API   (store.Accounts)
├── budgets/user-<id>.db   un budget par compte                                  (store.Store)
└── .secret_key           clé de signature des sessions
```

Chaque route du budget prend la base de l'utilisateur connecté (`require_budget`) : la requête d'un utilisateur n'ouvre jamais le fichier d'un autre. L'organisation de la 0.1 (un seul budget dans `budget_cockpit.db`) est migrée au démarrage : copiée dans le fichier du premier compte, vérifiée, puis supprimée.

## Le modèle

- **Paramètres** — premier mois affiché (`start_month`), horizon, solde du compte courant aujourd'hui, épargne aujourd'hui, seuil de sécurité, devise, langue.
- **Ligne** — `kind` (rentrée · dépense · épargne), `group` facultatif, et une règle :
  - `monthly` : `amount` tous les mois
  - `yearly` : `amount` le mois `month`
  - `profile` : douze montants, un par mois de l'année
  - `manual` : zéro tant qu'aucun montant n'est saisi
  - `yearly_table` : `{année: montant annuel}`, réparti en montant / 12 ; la dernière année indiquée continue
  - mois de début et de fin facultatifs (inclus), indicateur `active`
- **Exception** — `(ligne, mois) → montant`, saisie dans la grille. `paid = true` signifie que le mouvement a déjà eu lieu : il est dans le solde du jour, il compte donc pour zéro.

## La projection

Pour chaque mois *m* de l'horizon :

```
case(ligne, m)  = exception s'il y en a une, sinon règle(ligne, m)
reste(m)        = reste(m − 1) + rentrées(m) − dépenses(m) − épargne(m)    (reste(−1) = solde du jour)
alerte(m)       = découvert si reste(m) < 0, attention si reste(m) < seuil
```

Tous les montants sont des `Decimal`, jamais arrondis pendant le calcul, seulement à l'affichage. **Clôturer un mois** avance `start_month`, supprime les exceptions du mois clôturé et enregistre les soldes réels comme nouveau point de départ.

## Sécurité

Voir [SECURITY.md](../../SECURITY.md).

## Tests

```bash
python -m pip install -e ".[dev]"
ruff check src tests
python -m pytest
```

Les tests n'utilisent que des données fictives. Ne jamais committer de vraies données financières : `data/` et les sauvegardes sont ignorés par git.
