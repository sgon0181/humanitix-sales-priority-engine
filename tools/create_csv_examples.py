"""Author example CSV inputs with real sourced identities and labelled demo CRM/reviews.

This is fixture preparation, never part of the one-command runtime or approval logic.
"""
from copy import deepcopy
import csv
import json
from pathlib import Path

from priority_engine.__main__ import load
from priority_engine.engine import build_queue
from priority_engine.from_csv import COLUMNS, read_csv
from priority_engine.commercial import FIELDS as COMMERCIAL_FIELDS
from priority_engine.operations import prepare_work
from priority_engine.validation import validate_crm, validate_public

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_COLUMNS = ["account_id", "organisation_name", "website", "as_of", "crm_context_origin",
                    *COMMERCIAL_FIELDS, "crm_match_state", "crm_relationship", "restriction_state",
                    "last_interaction_at", "last_interaction_outcome", "last_interaction_channel"]


def add_commercial_examples(rows):
    """Fictional assumptions for demonstration, never sourced organiser metrics."""
    assumptions = {
        "BATCH_01": (48, 50, 60, "low"),
        "BATCH_02": (72, 100, 25, "medium"),
        "BATCH_03": (1, 10000, 200, "high"),
        "BATCH_04": (36, 30, 100, "high"),
        "BATCH_05": (4, 100, 100, "medium"),
        "BATCH_06": (24, 16, 1000, "high"),
        "BATCH_07": (120, "", 25, "unknown"),
        "BATCH_08": (12, 40, 20, "unknown"),
        "BATCH_NEW_09": (12, 100, 300, "medium"),
    }
    for row in rows:
        row.update(dict(zip(COMMERCIAL_FIELDS, assumptions.get(row["account_id"], ("", "", "", "unknown")))))
    return rows


def write(path, rows, columns=COLUMNS):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})


def add_demo_review_snapshots(path, rows):
    """Explicitly authored demo reviews of these inputs, not automatic runtime approval."""
    write(path, rows)
    imported = read_csv(path)
    public, issues = validate_public(imported["public"])
    contexts, cissues = validate_crm(imported["crm"], {a["account_id"] for a in public}, imported["clock"])
    if issues or cissues:
        raise ValueError("Invalid example facts")
    core = build_queue(public, contexts, imported["clock"], load(ROOT/"priority_engine/rules.json"), "CSV_PREVIEW")
    items = {i["account_id"]: i for i in prepare_work(core, contexts, load(ROOT/"priority_engine/workplan_rules.json"))}
    for row in rows:
        row.update(review_decision="accept", review_fingerprint=items[row["account_id"]]["review_fingerprint"],
                   reviewer_id="SIM_CSV_REVIEWER", review_origin="synthetic_fixture",
                   reviewed_at="2026-10-22T13:30:00Z",
                   review_reason="Explicitly authored example review; not actual user or Humanitix approval")
        if row["account_id"] in {"BATCH_05", "BATCH_NEW_09"}:
            row.update(review_decision="evidence-needed", requested_evidence="Verify identity and a current paid offer at the public organiser website")
        if row["account_id"] == "BATCH_07":
            row.update(review_decision="hold", reopen_condition="Existing relationship owner requests a separate review")
    write(path, rows)


def create_examples():
    destination = ROOT / "data/csv_examples"
    destination.mkdir(parents=True, exist_ok=True)
    public = {a["account_id"]: a for a in load(ROOT/"data/public_organisers.json")["organisers"]}
    rows = []
    for n, (source_id, owner) in enumerate([
        ("US001", "SIM_REP_A"), ("US035", ""), ("US008", "SIM_REP_C"), ("US014", "SIM_REP_B"),
        ("US011", "SIM_REP_E"), ("US049", "SIM_REP_D"), ("US080", "SIM_REP_A"), ("US040", "SIM_REP_B")], 1):
        source = public[source_id]
        row = {"account_id": f"BATCH_{n:02}", "organisation_name": source["organisation_name"], "website": source["website"],
               "as_of": "2026-10-22T14:00:00Z", "crm_context_origin": "synthetic", "owner_id": owner,
               "crm_match_state": "matched", "crm_relationship": "prospect", "restriction_state": "no_restriction_in_scenario",
               "source_record_ids_json": json.dumps(source["source_record_ids"]), "source_urls_json": json.dumps(source["source_urls"])}
        for key, value in source["imported_claims"].items():
            if key in COLUMNS and value is not None:
                row[key] = value
        if source["public_evidence"]:
            ev = deepcopy(source["public_evidence"])
            ev["account_id"] = row["account_id"]
            row["public_evidence_json"] = json.dumps(ev)
        rows.append(row)
    rows[2].update(last_interaction_at="2026-10-21T14:00:00Z", last_interaction_outcome="positive_reply", last_interaction_channel="email")
    rows[3].update(callback_requested_at="2026-10-19T14:00:00Z", callback_due_at="2026-10-22T13:00:00Z", callback_channel="phone")
    rows[6]["crm_relationship"] = "customer"
    rows[7]["restriction_state"] = "unknown"
    add_demo_review_snapshots(destination/"organisers_before.csv", rows)
    updated = deepcopy(rows)
    updated[1].update(callback_requested_at="2026-10-19T14:00:00Z", callback_due_at="2026-10-22T12:00:00Z", callback_channel="phone")
    updated[2]["restriction_state"] = "global_do_not_contact"
    updated.append({"account_id":"BATCH_NEW_09", "organisation_name":"Storytelling with Data", "website":"https://www.storytellingwithdata.com/",
                    "as_of":"2026-10-22T14:00:00Z", "crm_context_origin":"synthetic", "owner_id":"SIM_REP_A",
                    "crm_match_state":"matched", "crm_relationship":"prospect", "restriction_state":"no_restriction_in_scenario",
                    "source_record_ids_json":json.dumps(["legacy:PROSPECTS.md:P02"]),
                    "source_urls_json":json.dumps(["https://www.storytellingwithdata.com/"]),
                    "uncertainty_notes":"Preserved legacy reference identity, absent from merged 95. Not reverified for this CSV demonstration."})
    add_demo_review_snapshots(destination/"organisers_updated.csv", updated)
    # A practical flat template needs no embedded JSON and carries no approval.
    write(destination/"organisers_template.csv", [], TEMPLATE_COLUMNS)
    unreviewed=[{c:v for c,v in row.items() if not c.startswith("review") and c not in {"requested_evidence","reopen_condition","reopen_at"}} for row in updated]
    write(destination/"new_organisers.csv",add_commercial_examples(unreviewed))
    original_unreviewed=[{c:v for c,v in row.items() if not c.startswith("review") and c not in {"requested_evidence","reopen_condition","reopen_at"}} for row in rows]
    write(destination/"before_new_organisers.csv",original_unreviewed)
    return destination


if __name__ == "__main__":
    print(create_examples())
