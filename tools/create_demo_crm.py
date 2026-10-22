"""Author reproducible, explicitly fictional CRM inputs, without scoring accounts."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLOCK = datetime(2026, 10, 22, 14, tzinfo=timezone.utc)
LABEL = "SIMULATED CRM CONTEXT. NOT HUMANITIX DATA OR REAL ORGANISER HISTORY."


def event(row, outcome, days_ago, channel="phone"):
    item = {"interaction_id": f"SIM_INTERACTION_{row['account_id']}_{len(row['interactions']) + 1}",
            "occurred_at": (CLOCK - timedelta(days=days_ago)).isoformat(),
            "channel": channel, "outcome": outcome, "synthetic": True,
            "note": "Fictional scenario event; no actual contact occurred"}
    row["interactions"].append(item)
    return item


def callback(row, hours_from_clock):
    item = event(row, "callback_requested", 3)
    row["callback"] = {"requested_at": item["occurred_at"],
                       "due_at": (CLOCK + timedelta(hours=hours_from_clock)).isoformat(),
                       "channel": "phone", "status": "open",
                       "source_interaction_id": item["interaction_id"], "synthetic": True}


def make_fixtures(public_document):
    rows = []
    for account in sorted(public_document["organisers"], key=lambda a: a["account_id"]):
        aid = account["account_id"]
        number = int(aid.removeprefix("US"))
        row = {"account_id": aid, "scenario_id": "SIM_BASELINE", "synthetic": True,
               "simulated_match_state": "matched", "simulated_relationship": "prospect",
               "existing_company_record_id": "SIM_COMPANY_" + aid,
               "owner_id": "SIM_REP_" + "ABCDE"[(number - 1) % 5],
               "restriction_state": "no_restriction_in_scenario", "restricted_channels": [],
               "interactions": [], "callback": None, "defer_until": None,
               "open_review_task_id": None, "review_state": "unreviewed",
               "reviewer_id": None, "reviewed_at": None, "review_reason": None,
               "allowed_scope": "internal_demo_only"}
        # Fixed twelve-way rotation uses ID only, never research, prices or desired rank.
        remainder = number % 12
        if remainder == 0:
            row.update(simulated_match_state="unknown", simulated_relationship="unknown", restriction_state="unknown")
            if number % 24 == 0:
                callback(row, -2)
        elif remainder == 2:
            callback(row, -1)
        elif remainder == 3:
            callback(row, 48)
        elif remainder == 4:
            event(row, "no_answer", 2)
        elif remainder == 5:
            row["restriction_state"] = "global_do_not_contact"
            event(row, "restriction_recorded", 1, "internal")
        elif remainder == 6:
            row["simulated_relationship"] = "customer"
        elif remainder == 7:
            row["simulated_relationship"] = "open_opportunity"
        elif remainder == 8:
            event(row, "positive_reply", 1, "email")
        elif remainder == 9:
            row.update(restriction_state="channel_restricted", restricted_channels=["phone"])
            callback(row, -1)
        elif remainder == 10:
            event(row, "not_interested", 10)
        elif remainder == 11:
            row.update(owner_id=None, open_review_task_id="SIM_TASK_" + aid)
        rows.append(row)
    baseline = {"metadata": {"synthetic": True, "label": LABEL, "scenario_id": "SIM_BASELINE",
                             "scenario_clock": CLOCK.isoformat(), "time_zone": "America/New_York",
                             "construction": "Twelve-state ID rotation; no public quality, volume, priority or desired-rank input",
                             "real_crm_clearance": "not_available"}, "accounts": rows}
    variants = {"baseline": baseline}
    due = deepcopy(baseline)
    due["metadata"]["scenario_id"] = "SIM_CALLBACK_DUE"
    target = next(r for r in due["accounts"] if r["account_id"] == "US035")
    callback(target, -1)
    for row in due["accounts"]:
        row["scenario_id"] = "SIM_CALLBACK_DUE"
    variants["callback_due"] = due
    blocked = deepcopy(due)
    blocked["metadata"]["scenario_id"] = "SIM_CALLBACK_RESTRICTED"
    target = next(r for r in blocked["accounts"] if r["account_id"] == "US035")
    target["restriction_state"] = "global_do_not_contact"
    event(target, "restriction_recorded", 0, "internal")
    for row in blocked["accounts"]:
        row["scenario_id"] = "SIM_CALLBACK_RESTRICTED"
    variants["callback_restricted"] = blocked
    return variants


if __name__ == "__main__":
    public = json.loads((ROOT / "data/public_organisers.json").read_text())
    destination = ROOT / "data/synthetic"
    destination.mkdir(parents=True, exist_ok=True)
    for name, document in make_fixtures(public).items():
        (destination / (name + ".json")).write_text(json.dumps(document, indent=2) + "\n")
    print("Created three labelled CRM scenario batches with 95 account contexts each; no public data changed.")
