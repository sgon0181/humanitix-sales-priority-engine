# Data and provenance

Prepared on 22 October 2026. The research contains **95 real organiser identities**. Selected observations on 20 records have field-level source reviews; 75 remain unreviewed. No account is fully verified or cleared for outreach by this dataset.

## Research files

| File | Purpose |
| --- | --- |
| [public_organisers.json](public_organisers.json) | Canonical identities, imported claims and separately reviewed observations |
| [public_organisers.csv](public_organisers.csv) | Readable projection of the research, not an app upload template |
| [source_claims.json](source_claims.json) | All 105 original provider records with source IDs and row provenance |
| [verification_sample.json](verification_sample.json) | 20 specific source reviews with URLs, dates, access limits and open questions |
| [raw CSV](raw/Humanitix_merged_research_unverified.csv) | Byte-for-byte original research supplied by Perplexity and ChatGPT/Apollo |
| [audit](../research/DATA_AUDIT.md) | Identity conflicts, evidence corrections and sample-selection rationale |

The original raw SHA-256 is `b537b8f5313fe96648404e08d9f8a77f398040a39cbc40c3a677d151e85cbf8b`. A test verifies its preservation.

## Interpretation

- An identity, advertised price, capacity or event listing is not proof of actual paid ticket sales, annual volume, buying authority or willingness to switch.
- A field review applies only to the observations explicitly recorded. It does not verify the entire account or its imported claims.
- Source-provider count is not independent corroboration. Providers may cite the same page.
- Unknown values stay null or blank. A missing value is not zero. A multi-session package or festival pass is one purchase unit.
- Actual annual sales, revenue, switching intent and real Humanitix CRM clearance remain unknown.

## Demonstration inputs

Use [csv_examples](csv_examples/README.md) for files accepted by the app. The 95-account upload preserves the research and adds **fictional commercial assumptions and simulated CRM history** in separate fields. The two 50-account batches are entirely fictional. No synthetic input becomes a verified public fact.

The [synthetic](synthetic/) folder contains fictional CRM scenarios, ownership, reviewer decisions and five-representative capacity fixtures. They are not records from Humanitix. Importing an account never grants contact permission or upgrades evidence quality.

Research findings are dated observations, not automatically refreshed intelligence. No autonomous discovery or enrichment happens during a CSV run.
