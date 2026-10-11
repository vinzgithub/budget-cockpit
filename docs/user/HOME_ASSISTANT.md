# Home Assistant integration

🇫🇷 [Version française](HOME_ASSISTANT.fr.md)

Budget Cockpit runs next to Home Assistant (not inside it) and exposes a **read-only API** protected by a key. Home Assistant reads it with the built-in [RESTful integration](https://www.home-assistant.io/integrations/rest/) — nothing to install.

## 1. Create a key

In Budget Cockpit: **Settings → Home Assistant and API → Create a key**. Copy the key immediately: it is shown only once.

## 2. Store it in `secrets.yaml`

```yaml
budget_cockpit_authorization: "Bearer PASTE-YOUR-KEY-HERE"
```

## 3. Add the sensors to `configuration.yaml`

```yaml
rest:
  - resource: http://budget-cockpit.lan:8080/api/v1/summary   # your Budget Cockpit address
    headers:
      Authorization: !secret budget_cockpit_authorization
    scan_interval: 900
    sensor:
      - name: Budget Cockpit end of month
        unique_id: budget_cockpit_balance_end_of_month
        value_template: "{{ value_json.balance_end_of_month }}"
        unit_of_measurement: "EUR"
        device_class: monetary
        json_attributes:
          - current_month
          - savings_total
          - safety_threshold
      - name: Budget Cockpit tightest month
        unique_id: budget_cockpit_lowest_balance
        value_template: "{{ value_json.lowest.balance }}"
        unit_of_measurement: "EUR"
        device_class: monetary
        json_attributes_path: "$.lowest"
        json_attributes:
          - month
      - name: Budget Cockpit alert
        unique_id: budget_cockpit_next_alert
        value_template: "{{ value_json.next_alert.level if value_json.next_alert else 'ok' }}"
        json_attributes:
          - next_alert
```

Restart Home Assistant. You get:

| Entity | Value |
|---|---|
| `sensor.budget_cockpit_end_of_month` | what will be left at the end of the current month |
| `sensor.budget_cockpit_tightest_month` | lowest forecast balance (attribute `month`) |
| `sensor.budget_cockpit_alert` | `ok`, `warning` (below your safety threshold) or `overdraft` |

## 4. Get notified

```yaml
automation:
  - alias: "Budget Cockpit: overdraft alert"
    triggers:
      - trigger: state
        entity_id: sensor.budget_cockpit_alert
        to: ["warning", "overdraft"]
    actions:
      - action: notify.mobile_app_my_phone   # your notify service
        data:
          title: "Budget Cockpit"
          message: >
            {{ 'Overdraft forecast' if trigger.to_state.state == 'overdraft' else 'Balance below your threshold' }}
            in {{ state_attr('sensor.budget_cockpit_alert', 'next_alert')['month'] }}.
```

## 5. A card

```yaml
type: entities
title: Budget Cockpit
entities:
  - sensor.budget_cockpit_end_of_month
  - sensor.budget_cockpit_tightest_month
  - sensor.budget_cockpit_alert
```

## Showing the whole Budget Cockpit interface inside Home Assistant (optional)

You can add Budget Cockpit as a **Webpage** dashboard in Home Assistant. For the browser to accept it:

- serve both over HTTPS, under the same domain (for example `ha.example.org` and `budget.example.org`), so the login cookie works inside the frame;
- allow Home Assistant to frame Budget Cockpit: `BUDGET_COCKPIT_FRAME_ANCESTORS="https://ha.example.org"`.

## API reference

`GET /api/v1/summary` — header `Authorization: Bearer <key>`.

Example response (made-up figures from the demo household):

```json
{
  "currency": "EUR",
  "current_month": "2026-10",
  "balance_end_of_month": 1115.0,
  "savings_total": 4450.0,
  "safety_threshold": 100.0,
  "alert": "ok",
  "next_alert": null,
  "lowest": { "month": "2026-11", "balance": 1040.0 },
  "months": [ { "month": "2026-10", "balance": 1115.0, "income": 3400.0, "expense": 3035.0, "savings": 250.0, "alert": "ok" } ],
  "groups": { "Holiday let": { "month": -1660.0, "year_to_date": -1660.0 } }
}
```

`alert` is the state of the current month; `next_alert` is the first month (current or future) with a warning or an overdraft, or `null`.
