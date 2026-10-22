# Generated example outputs

Generated from `data/csv_examples/real_organisers_95.csv` using the published engine and the fixed scenario clock. The CSV retains all 95 identities and labels simulated commercial/CRM information. Selected field reviews cover 20 records; 75 remain unreviewed.

- `ranked_accounts.csv`: the complete explained work order, not a list cleared for contact.
- `hubspot_tasks_REVIEW_DRAFT.csv`: 14 proposed internal tasks, all pending human review and ineligible for live export.
- `handoff_REVIEW_DRAFT.zip`: company, note and task drafts with mapping instructions. Actual HubSpot owner/association IDs are unresolved. Do not import directly.

These are demonstration outputs, not Humanitix data or measured sales results. Example fees and CRM context are fictional. Regenerate with `python3 run_csv.py data/csv_examples/real_organisers_95.csv`.
