# Intégration Home Assistant

🇬🇧 [English version](HOME_ASSISTANT.md)

Budget Cockpit tourne à côté de Home Assistant (pas dedans) et expose une **API en lecture seule**, protégée par une clé. Home Assistant la lit avec l'[intégration RESTful](https://www.home-assistant.io/integrations/rest/) déjà incluse : rien à installer.

## 1. Créer une clé

Dans Budget Cockpit : **Paramètres → Home Assistant et API → Créer une clé**. Copie-la tout de suite : elle n'est affichée qu'une fois. Une clé lit le budget du compte qui l'a créée : avec plusieurs comptes, chacun crée la sienne.

## 2. La ranger dans `secrets.yaml`

```yaml
budget_cockpit_authorization: "Bearer COLLE-TA-CLE-ICI"
```

## 3. Ajouter les capteurs dans `configuration.yaml`

```yaml
rest:
  - resource: http://budget-cockpit.lan:8080/api/v1/summary   # l'adresse de ton Budget Cockpit
    headers:
      Authorization: !secret budget_cockpit_authorization
    scan_interval: 900
    sensor:
      - name: Budget Cockpit reste fin de mois
        unique_id: budget_cockpit_balance_end_of_month
        value_template: "{{ value_json.balance_end_of_month }}"
        unit_of_measurement: "EUR"
        device_class: monetary
        json_attributes:
          - current_month
          - savings_total
          - safety_threshold
      - name: Budget Cockpit mois le plus serré
        unique_id: budget_cockpit_lowest_balance
        value_template: "{{ value_json.lowest.balance }}"
        unit_of_measurement: "EUR"
        device_class: monetary
        json_attributes_path: "$.lowest"
        json_attributes:
          - month
      - name: Budget Cockpit alerte
        unique_id: budget_cockpit_next_alert
        value_template: "{{ value_json.next_alert.level if value_json.next_alert else 'ok' }}"
        json_attributes:
          - next_alert
```

Redémarre Home Assistant. Tu obtiens :

| Entité | Valeur |
|---|---|
| `sensor.budget_cockpit_reste_fin_de_mois` | ce qu'il restera à la fin du mois en cours |
| `sensor.budget_cockpit_mois_le_plus_serre` | le reste prévu le plus bas (attribut `month`) |
| `sensor.budget_cockpit_alerte` | `ok`, `warning` (sous ton seuil de sécurité) ou `overdraft` (découvert) |

## 4. Être prévenu

```yaml
automation:
  - alias: "Budget Cockpit : alerte de découvert"
    triggers:
      - trigger: state
        entity_id: sensor.budget_cockpit_alerte
        to: ["warning", "overdraft"]
    actions:
      - action: notify.mobile_app_mon_telephone   # ton service de notification
        data:
          title: "Budget Cockpit"
          message: >
            {{ 'Découvert prévu' if trigger.to_state.state == 'overdraft' else 'Reste sous ton seuil' }}
            en {{ state_attr('sensor.budget_cockpit_alerte', 'next_alert')['month'] }}.
```

## 5. Une carte

```yaml
type: entities
title: Budget Cockpit
entities:
  - sensor.budget_cockpit_reste_fin_de_mois
  - sensor.budget_cockpit_mois_le_plus_serre
  - sensor.budget_cockpit_alerte
```

## Afficher toute l'interface de Budget Cockpit dans Home Assistant (facultatif)

Tu peux ajouter Budget Cockpit comme tableau de bord **Page web** dans Home Assistant. Pour que le navigateur l'accepte :

- sers les deux en HTTPS, sous le même domaine (par exemple `ha.exemple.fr` et `budget.exemple.fr`), pour que le cookie de connexion fonctionne dans le cadre ;
- autorise Home Assistant à afficher Budget Cockpit : `BUDGET_COCKPIT_FRAME_ANCESTORS="https://ha.exemple.fr"`.

## Référence de l'API

`GET /api/v1/summary` — en-tête `Authorization: Bearer <clé>`.

`alert` est l'état du mois en cours ; `next_alert` est le premier mois (en cours ou futur) en *Attention* ou en *Découvert*, ou `null`. Le détail des champs est dans la [version anglaise](HOME_ASSISTANT.md#api-reference).
