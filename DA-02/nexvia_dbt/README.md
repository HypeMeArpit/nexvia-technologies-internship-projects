# nexvia_dbt

dbt project for the Customer 360 & Marketing Attribution platform. See the main README for setup.

| Layer | Models | What it does |
|---|---|---|
| staging | `stg_*` | Renames and casts the raw thelook and synthetic tables (views) |
| intermediate | `int_events__user_resolved`, `int_sessions` | Gives each event its session's user, maps traffic source to a marketing channel, rolls events up to sessions |
| marts | `fct_sessions`, `fct_orders` | Session and order facts |
| marts | `mart_customer_rfm` | RFM scores, predicted 12-month CLV, customer segment |
| marts | `mart_marketing_attribution`, `mart_attribution_by_model` | Four attribution models, spend, CPA, ROAS, ROI |
| analytics | `dim_customers` | Customer 360: one row per customer |
| analytics | `fct_marketing_spend` | Daily spend per channel |

Singular tests in `tests/` check that attribution revenue reconciles to order revenue,
that RFM revenue matches source revenue, and that each session has at most one user.

Run from this folder:

```bash
dbt deps
dbt build
```
