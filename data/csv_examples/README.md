# CSV examples

Upload one CSV in the local app, or run `python3 run_csv.py path/to/accounts.csv`. The program derives priorities and next actions; do not supply scores or priority columns.

## Pick an input

| File | Rows | Contents |
| --- | ---: | --- |
| [real_organisers_95.csv](real_organisers_95.csv) | 95 | Real identities and preserved research; simulated commercial and CRM inputs |
| [fully_synthetic_organisers.csv](fully_synthetic_organisers.csv) | 50 | Fictional organisers, commercial inputs and CRM histories |
| [fully_synthetic_organisers_set_2.csv](fully_synthetic_organisers_set_2.csv) | 50 | Independent fictional batch with different identities and histories |
| [new_organisers.csv](new_organisers.csv) | 9 | Small real-identity example showing callback, restriction and research cases |
| [organisers_template.csv](organisers_template.csv) | 0 | Blank 15-field starter |

All commercial figures in these examples are fictional testing assumptions, not estimates of the named businesses. The source-backed file retains exactly 20 field reviews; the other 75 records remain unreviewed. Additional before/after CSVs support automated regression tests and generator examples.

The example clock is fixed to 22 October 2026. Re-running a file reproduces that scenario, not today's contact eligibility. The UI shows the top 10 accounts; results downloads contain all rows.

## The 15 essential fields

| Field | Supply |
| --- | --- |
| `account_id` | Unique stable account ID |
| `organisation_name` | Organiser name |
| `website` | Organiser website |
| `as_of` | One consistent ISO timestamp for the scenario |
| `crm_context_origin` | `synthetic` or `provided_unverified` |
| `simulated_annual_paid_events` | Annual paid-event assumption, or blank |
| `simulated_paid_tickets_per_event` | Paid-ticket assumption, or blank |
| `simulated_average_ticket_price_usd` | Average USD ticket-price assumption, or blank |
| `simulated_switching_difficulty` | `low`, `medium`, `high` or `unknown` |
| `crm_match_state` | Known CRM match state, or blank |
| `crm_relationship` | Known relationship state, or blank |
| `restriction_state` | Known contact-restriction state, or blank |
| `last_interaction_at` | ISO timestamp, or blank |
| `last_interaction_outcome` | Recorded outcome, or blank |
| `last_interaction_channel` | Recorded channel, or blank |

Supply the three last-interaction fields together, or leave all three blank. Do not label unknown clearance as unrestricted. Missing commercial inputs produce Unknown. Data-origin labels are required and do not confer real CRM clearance.

Callbacks, channel restrictions, ownership, source evidence, full interaction history and existing review records are optional advanced columns. Follow an advanced example for supported values; the complete accepted-column list is `COLUMNS` in [from_csv.py](../../priority_engine/from_csv.py). Arbitrary CRM exports need mapping to this format. Supplied review decisions remain bound to their original snapshot and are not automatically renewed.
