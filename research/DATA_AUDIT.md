# Merged organiser research audit

Checked 2026-10-22. Scope: supplied research consolidation, a purposive 20-record primary-source sample and data preparation. No new prospect discovery or application implementation.

## Findings

The input has 95 unique IDs and 68 columns. There are no exact normalized-name or normalized-domain duplicate groups. This does not exclude parent/brand overlap. Chicago Music Box Theatre (US070) and Houston Music Box Theater (US081) are distinct organisations with different addresses and programmes; retain both.

There are 40 Perplexity-only records, 45 ChatGPT/Apollo-only records and 10 with both, producing 105 provider records. The original `source_count` measures provider count, not independent primary-source confirmation. All imported research dates are 2026-10-22; that is not the event date or proof of freshness.

| Missing merged field | Records | Commercial implication |
| --- | ---: | --- |
| Advertised ticket price | 32 | No price observation available from the merged field |
| Ticketing provider | 59 | Provider and scope require checking before a migration hypothesis |
| Ticketing authority text | 21 | Filled text in other records is also frequently inference or an explicit unknown |
| City | 2 | Headquarters/venue/online audience must be distinguished |
| State | 3 | US scope requires evidence at the relevant event level |

None of the 95 records establishes structured annual paid-ticket volume, annual ticket revenue, actual buying authority, switching intent or real CRM eligibility. Do not derive these by multiplying an advertised price, capacity and a calendar count. The dataset is sufficient to demonstrate evidence handling and sales decision rules with simulated CRM context. It cannot validate expected revenue, conversion or which segment is economically strongest.

## Conflicting claims and identity handling

| Record | Resolution or retained uncertainty |
| --- | --- |
| US008 Big Ears | `.org` and `.com` show matching information. Front Gate primary sales and Ticketmaster Exchange resale are different roles. Price waves and pass units are retained separately |
| US009 Brew Ha Ha | `www` variation is formatting. Anaheim and McKinney are two office observations. Silverado headline, checkout and promotional prices conflict; no representative current price selected |
| US055 Pickathon | Portland office and Happy Valley festival venue explain the city difference. Keep both scoped locations |
| US080 The Moth | Domain variant and local-versus-multiple-market scope do not establish different entities. Venue-level control limits organisation-wide ticketing assumptions |

The four input conflict flags remain visible after reconciliation. Do not silently erase source disagreement. No rows were merged or removed. Henceforth's legal-name alias is recorded without creating a second account.

## Twenty-record primary-source sample

Selection deliberately covers both source providers, all four flagged records, near-name identities, original reference cases, cultural events, festivals, classes, professional training and conferences. It targets decision risks; it is not statistically representative. Nineteen records yielded some direct primary content; ASMP relied on indexed primary content. Several individual fields remain indexed, unavailable, historical or unresolved. None is certified as a fully verified account.

| ID and organiser | Decision-critical observation | Primary source |
| --- | --- | --- |
| US001 18 Reasons | Paid classes coexist with free programmes; storefront is not proof of ticketing authority | [Programme](https://18reasons.org/) |
| US002 Aravaipa Running | Repeated US races, including outside producers; calendar total is not owned paid inventory | [Races](https://www.aravaiparunning.com/races/) |
| US003 ASMP New York | Historical member/non-member workshop pricing; eight places is capacity. Indexed source only | [Workshop](https://www.asmp.org/newyork/event/fine-art-printing-workshop/) |
| US008 Big Ears | One festival pass spans multiple days; primary and resale providers differ | [Passes](https://bigearsfestival.org/passes/) |
| US009 Brew Ha Ha | Event checkout prices conflict and current date is unresolved | [Checkout](https://brewhahaproductions.ticketspice.com/silverado) |
| US010 Brooklyn Brainery | Mixed programme; published class volume is self-reported and not paid attendance | [Programme](https://brooklynbrainery.com/) |
| US014 Milk Street | Eventbrite links observed; membership-included livestreams differ from individual sales | [School](https://www.177milkstreet.com/milk-street-cooking-school) |
| US019 Comedy Party | Eventbrite links observed; month/day labels do not resolve event year | [Organiser](https://www.comedy-party.com/) |
| US035 Henceforth Comedy | Separate paid and free shows; shared legal-name alias | [Organiser](https://henceforthcomedy.com/) |
| US040 Kickstand Comedy | Portland programme corroborated; embedded details do not establish current prices | [Calendar](https://www.kickstandcomedy.org/calendar) |
| US049 Noble Desktop | Repeated paid course dates; a two-day bootcamp is one package | [Training](https://www.nobledesktop.com/corporate-training/powerpoint) |
| US055 Pickathon | Office and festival venue explain geographic disagreement; current edition differs from older blogs | [Organiser](https://pickathon.com/) |
| US059 PyTexas | Reports 275 in-person and 14 virtual ticket sales for one event; annual total remains unknown | [Conference](https://www.pytexas.org/conference/) and [registration runbook](https://www.pytexas.org/runbooks/conference/runbooks/registration/) |
| US069 Sixth & I | In-person, virtual and book bundles have different purchase units | [Event](https://www.sixthandi.org/event/sophia-bush/) |
| US070 Chicago Music Box | Standard cinema rates and Chicago address distinguish it from Houston | [Box office](https://www.musicboxtheatre.com/about/ticket-box-office-information) |
| US078 The Management Center | FAQ confirms registration constraints; current catalogue prices were not reverified | [FAQ](https://www.managementcenter.org/faqs/) |
| US080 The Moth | Venue determines authorised seller; one event's provider is not an organisation-wide contract | [FAQ](https://www.themoth.org/about/faq) |
| US081 Houston Music Box | Different organisation from Chicago; general and reserved seating require distinct treatment | [Tickets](https://www.themusicboxtheater.com/tickets) |
| US087 Training for Change | Upcoming waitlists and historical sliding-scale prices differ; some detail pages unavailable | [Organiser](https://www.trainingforchange.org/) |
| US095 Write the Docs | Portland 2026 is over; historical in-person and virtual tiers do not prove current inventory | [Tickets](https://www.writethedocs.org/conf/portland/2026/tickets/) |

Exact fields, dates, access basis, URLs and limitations are in `data/verification_sample.json`. Retained research claims are in `data/source_claims.json`; the raw CSV remains byte-identical. A verified page observation does not verify every imported claim on that row.

## Consequences for the engine

Use all 95 identities as a substantial review queue, with visible evidence status. The 75 unreviewed records need internal/public review rather than confident call recommendations. Reviewed paid offers can support a recurring-paid activity signal, but its strength is not a dollar forecast. Missing volume must remain unknown. Historic and undated evidence should produce a refresh action.

Keep synthetic CRM ownership, interactions, restrictions, callbacks and capacity in a separate scenario file. They can change immediate actions without rewriting public commercial evidence. Every real record remains pending CRM clearance. The previous five accounts are regression cases; Greatest Show Ever, Drunk Theatre and Storytelling with Data are absent from the merged CSV and remain separately preserved, not silently inserted into its 95 rows.

No segment winner, revenue uplift or sales-ready lead count is established by this audit. The useful demonstration is a reproducible, reviewable decision queue with honest unknowns and an explicit handoff gate.

## Preparation checks completed

The original hash matches the preserved raw file. All 95 IDs and 105 provider joins survive preparation; provider-specific fields match the original after the documented punctuation normalization. The CSV round trip matches all 95 rows and 26 fields. Review-file joins, null commercial fields, unavailable real clearance, separate synthetic design and all five reference mappings were checked. Generated/maintained text passes the punctuation rule. Results are in `data/quality_checks.json`. These are data checks; no priority engine or application tests have run.
