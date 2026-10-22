"""Clearly fictional reviewer decisions and five-rep capacity, not real approvals."""
import json
from pathlib import Path

from priority_engine.engine import build_queue
from priority_engine.operations import decision_record, prepare_work
from priority_engine.validation import timestamp, validate_crm, validate_public

ROOT = Path(__file__).resolve().parents[1]


def make_demo(core, contexts, policy):
    items = prepare_work(core, contexts, policy)
    decisions = []
    for item in items:
        number = int(item["account_id"].removeprefix("US"))
        # An authored fixture rotation, independent of value, queue and desired rank.
        state = {0: "accept", 1: "accept", 2: "evidence_needed", 3: "hold", 4: "accept"}.get(number % 7)
        if state is None:
            continue
        if item["work_kind"] == "compliance_review":
            question = "Resolve identity, existing relationship and contact restrictions using authorised internal records"
        elif item["qualification"]["unresolved_checks"]:
            question = item["qualification"]["unresolved_checks"][0]["action"]
        else:
            question = "Confirm consistently scoped annual paid-ticket volume and ticketing control; keep unknowns explicit"
        decisions.append(decision_record(
            item, state, "SIM_REVIEWER_1", "Fictional review decision for demonstration; not an actual user or Humanitix approval",
            "2026-10-22T13:30:00+00:00",
            requested_evidence=question if state == "evidence_needed" else None,
            reopen_condition="A reviewer resolves the recorded evidence gap" if state == "hold" else None,
            source="synthetic_fixture"))
    ledger = {"metadata": {"synthetic": True, "label": "SIMULATED HUMAN REVIEW DECISIONS. NOT ACTUAL USER OR HUMANITIX APPROVALS.",
                           "construction": "Seven-way stable ID rotation, independent of public activity and allocation results"},
              "decisions": decisions}
    capacity = {"metadata": {"synthetic": True, "label": "FICTIONAL FIVE-PERSON CAPACITY. NOT HUMANITIX STAFF OR WORKLOADS.",
                             "session": "Remaining minutes from the fixed scenario clock; no calendar availability claim"},
                "representatives": [{"rep_id": "SIM_REP_" + name, "name": "Demo rep " + name, "available_minutes": minutes}
                                    for name, minutes in zip("ABCDE", [60, 45, 30, 0, 60])]}
    return ledger, capacity


if __name__ == "__main__":
    public = json.loads((ROOT / "data/public_organisers.json").read_text())
    crm = json.loads((ROOT / "data/synthetic/baseline.json").read_text())
    rules = json.loads((ROOT / "priority_engine/rules.json").read_text())
    policy = json.loads((ROOT / "priority_engine/workplan_rules.json").read_text())
    accounts, pissues = validate_public(public)
    clock = timestamp(crm["metadata"]["scenario_clock"])
    contexts, cissues = validate_crm(crm, {a["account_id"] for a in accounts}, clock)
    if pissues or cissues:
        raise ValueError("Resolve input issues before authoring demo review snapshots")
    core = build_queue(accounts, contexts, clock, rules, crm["metadata"]["scenario_id"])
    ledger, capacity = make_demo(core, contexts, policy)
    for name, value in [("reviews.json", ledger), ("capacity.json", capacity)]:
        (ROOT / "data/synthetic" / name).write_text(json.dumps(value, indent=2) + "\n")
    print("Created labelled synthetic review and capacity fixtures; no real approval or sales activity occurred.")
