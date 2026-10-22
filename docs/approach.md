# Commercial approach

## Start with the sales decision

The opportunity is to help a small sales team choose useful work from an existing organiser database. Automated discovery, machine learning and live CRM integration would add complexity before proving that prioritisation helps.

The project compared recurring cultural/performance organisers with professional workshops. Both can have recurring paid inventory; neither segment is proven economically superior by this research. Annual paid volume, ticketing control, switching barriers and acquisition effort still need qualification.

The public research contains 95 identities assembled from Perplexity and ChatGPT/Apollo. Selected primary-source observations were checked on 20 records. This purposive sample is not representative validation. The [audit](../research/DATA_AUDIT.md) and [structured field reviews](../data/verification_sample.json) preserve corrections, sources, dates and limitations.

## Keep value and urgency separate

1. Apply the existing CRM rules to identify the action category and relevant timing.
2. Inside comparable work, order by illustrative annual gross fee revenue, highest first.
3. Use switching difficulty only to break equal-fee ties. Unknown fees follow known fees; GMV is not a substitute.
4. Preserve public activity signals, unresolved checks and stable IDs as the remaining tie-breaks.

The arithmetic is deliberately simple:

```text
Annual paid tickets = paid events/year x paid tickets/event
Annual GMV = annual paid tickets x average ticket price
Annual gross fees = annual paid tickets x (ticket price x fee percentage / 100 + fee per ticket)
```

At illustrative inputs of 12 events, 200 paid tickets per event, $40 tickets and 4% + $1 per ticket, annual GMV is $96,000 and gross fees are $6,240. These figures are simulated, not verified revenue or profit; processing costs are not deducted. Edit [commercial_settings.json](../priority_engine/commercial_settings.json) to change the assumptions. The configurable High/Medium/Low GMV bands are descriptive, unvalidated demonstration labels; they do not determine fee ranking.

A due callback needs review. An unknown CRM identity needs clarification. A restriction blocks contact. A recent unanswered attempt can require waiting. None of these states is cancelled by a larger fee estimate. Missing public evidence creates a bounded research task, not a sales-ready lead.

## Fit the existing sales operation

One CSV produces a ranked work view and company/note/task review drafts. The interface shows 10 rows for readability; the download retains every account. Existing ownership and five fictional representatives' available capacity remain in the underlying workflow, without turning the interface into a workforce optimiser. Proposed tasks do not create approvals or permission to contact.

The local application uses a standard-library Python HTTP server and plain HTML, CSS and JavaScript. No model, paid service or live HubSpot access is needed. [Tests](../tests) exercise the commercial rules and operational boundaries; they do not prove business performance.

## What a real pilot would test

Use an authorised CRM sample, map its actual properties, validate commercial inputs and agree contact policy with the sales team. Compare the prioritised workflow with the current method over a bounded period, including research/preparation time in the denominator. Measure qualified conversations per sales hour first, then acquired/activated hosts, paid-ticket volume and fee contribution. Revenue lift, conversion probability and optimal allocation are currently unmeasured.

## Files worth inspecting

| Location | Purpose |
| --- | --- |
| `priority_engine/commercial.py` | Explicit commercial arithmetic and fee ordering |
| `priority_engine/engine.py` | Public-evidence qualification and CRM action rules |
| `priority_engine/operations.py` | Unified work order, human decisions and capacity |
| `priority_engine/demo_server.py` | CSV upload, presentation adapter and downloads |
| `demo/` | Three-slide introduction and one ranked table |
| `data/` | Research provenance, review observations and labelled fixtures |
| `tests/` | Correctness checks and original-core fingerprint fixture |

The locally bundled Montserrat font is distributed under its [SIL Open Font License](../demo/fonts/OFL.txt).
