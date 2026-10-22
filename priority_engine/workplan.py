"""Milestone 2 CLI: build a local plan or append an explicit review decision."""
import argparse
import csv
import io
import json
from pathlib import Path

from .__main__ import ROOT, digest, load, md, save_json
from .engine import build_queue
from .handoff import build_handoff
from .operations import build_workplan, decision_record, prepare_work, resolve_reviews
from .validation import timestamp, validate_crm, validate_public


def load_core(args):
    public, crm_doc, rules = load(args.public), load(args.crm), load(args.rules)
    clock = timestamp(args.as_of or crm_doc["metadata"]["scenario_clock"])
    for field in ("evidence_freshness_days", "offer_window_days", "cooldown_days", "attempt_window_days", "attempt_limit", "reply_window_days"):
        if type(rules[field]) is not int or rules[field] < 1:
            raise ValueError("Rule setting must be a positive integer: " + field)
    accounts, pissues = validate_public(public)
    contexts, cissues = validate_crm(crm_doc, {a["account_id"] for a in accounts}, clock)
    core = build_queue(accounts, contexts, clock, rules, crm_doc["metadata"]["scenario_id"])
    core["metadata"]["provenance"] = {
        "public_input_sha256": digest(args.public), "crm_input_sha256": digest(args.crm),
        "rules_sha256": digest(args.rules), "original_raw_sha256": public.get("metadata", {}).get("source_sha256")}
    return core, contexts, pissues + cissues


def write_csv(path, rows, columns):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k) for k in columns})
    path.write_text(out.getvalue(), encoding="utf-8", newline="")


def write_plan(plan, handoff, core, issues, output):
    output.mkdir(parents=True, exist_ok=True)
    save_json(output / "workplan.json", plan)
    save_json(output / "handoff_draft.json", handoff)
    save_json(output / "core_queue.json", core)
    save_json(output / "validation_report.json", {"status": "passed" if not issues and not plan["review_issues"] else "completed_with_issues",
                                                "input_issues": issues, "review_issues": plan["review_issues"]})
    columns = ["unified_rank", "account_id", "organisation_name", "priority_band", "priority_timing", "underlying_queue",
               "underlying_queue_rank", "work_kind", "execution_scope", "work_title", "review_state", "estimated_minutes",
               "existing_owner_id", "assigned_rep_id", "rep_sequence", "planned_start_at", "planned_end_at",
               "allocation_status", "allocation_reason", "review_fingerprint", "synthetic", "eligible_for_live_export"]
    write_csv(output / "unified_queue.csv", plan["work"], columns)
    write_csv(output / "unassigned.csv", [i for i in plan["work"] if i["allocation_status"] == "unassigned"], columns)
    write_csv(output / "representatives.csv", plan["representatives"], ["rep_id", "name", "available_minutes", "assigned_minutes", "remaining_minutes"])
    for kind in ("companies", "notes", "tasks"):
        rows = [{"Local draft key": r["draft_key"], "Company domain name": r["association_company_domain"],
                 **r["standard_properties"], "Simulation only": True, "Eligible for live export": False} for r in handoff[kind]]
        standard = {"companies": ["Name", "Company domain name"], "notes": ["Note body", "Activity date"],
                    "tasks": ["Task title", "Due date", "Task type", "Task status", "Activity assigned to", "Notes"]}[kind]
        headers = list(dict.fromkeys(["Local draft key", "Company domain name"] + standard + ["Simulation only", "Eligible for live export"]))
        write_csv(output / (kind + "_DRAFT.csv"), rows, headers)
    lines = ["# Unified sales work plan", "", plan["metadata"]["label"], "",
             "Sales activity means preparation only. Nothing here authorizes a call or message.", "",
             "Priority: due commitments, recent replies, qualification, research, clearance, policy review, waiting, existing relationships. Original classifications and rule traces are retained.", "",
             "Only explicit accept decisions or specific evidence-needed research requests can be assigned. Holds, missing/stale decisions, restrictions, future waits and existing relationships have visible reasons. Allocation follows global rank; owned work stays with its owner, unowned work uses the most remaining minutes.", "",
             "## Simulated five-person capacity", "",
             "| Rep | Available minutes | Assigned minutes | Remaining minutes | Tasks |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for rep in plan["representatives"]:
        lines.append(f"| {rep['name']} | {rep['available_minutes']} | {rep['assigned_minutes']} | {rep['remaining_minutes']} | {len(rep['assignments'])} |")
    lookup = {i["work_key"]: i for i in plan["work"]}
    for rep in plan["representatives"]:
        lines += ["", "## " + rep["name"] + ": next work", ""]
        if not rep["assignments"]:
            lines.append("No assignment. Capacity or approval constraints remain visible in the unified queue.")
        for key in rep["assignments"]:
            item = lookup[key]
            lines.append(f"{item['rep_sequence']}. Rank {item['unified_rank']}, {item['account_id']} ({item['organisation_name']}), {item['estimated_minutes']} minutes, {item['work_kind']}: {item['work_title']}. Why: {item['priority_band']}; {item['allocation_reason']}.")
    lines += ["", "## Unified priority queue, all accounts", "",
              "| Rank | Account | Original queue | Work kind | Review | Rep | Minutes | Allocation or waiting reason |",
              "| --- | --- | --- | --- | --- | --- | ---: | --- |"]
    for item in plan["work"]:
        lines.append("| " + " | ".join(md(v) for v in [item["unified_rank"], item["account_id"] + ", " + item["organisation_name"],
            item["underlying_queue"], item["work_kind"], item["review_state"], item["assigned_rep_id"] or "Unassigned",
            item["estimated_minutes"], item["allocation_reason"]]) + " |")
    lines += ["", "## Handoff draft", "", "handoff_draft.json contains separated public research, simulated CRM, review history and assignment. Company, note and task draft CSVs are review aids only. Actual owner/record IDs are unresolved and all live-export flags are false. See HANDOFF_MAPPING.md in this output directory.", ""]
    (output / "workplan.md").write_text("\n".join(lines), encoding="utf-8")
    (output / "HANDOFF_MAPPING.md").write_text(MAPPING, encoding="utf-8")


MAPPING = """# Review-only HubSpot mapping

SIMULATED CONTEXT. DO NOT IMPORT. Every row is ineligible for live export.

| Draft | Proposed HubSpot fields | Resolve before any real handoff |
| --- | --- | --- |
| Company | Name, Company domain name | Confirm identity, existing Company Record ID and preserve its owner |
| Note | Note body, Activity date; company association | Reconcile the existing POC note; preserve evidence and simulation labels |
| Task | Task title, Due date, Task type=To-Do, Task status=Not started, Notes, Activity assigned to; company association | Resolve actual task/owner IDs and task existence; retain the existing owner |

Local draft keys and simulation flags are review metadata, not HubSpot properties. Fictional representative IDs never populate actual owner fields. Scheduled task end is the draft due time; an original callback deadline stays separate in JSON. No completed calls, emails, deals, revenue or lifecycle updates are drafted.

Local stable keys make repeated file generation reproducible. They do not make activity imports idempotent. Existing simulated task IDs indicate manual reconciliation/update after actual-ID lookup, not a repeatable activity import. Existing company ownership is never overwritten by an unowned task assignment.

Official documentation checked 2026-10-22 supports the field mapping: [file requirements](https://knowledge.hubspot.com/import-and-export/set-up-your-import-file), [import behaviour](https://knowledge.hubspot.com/import-and-export/understand-the-import-tool), [activity fields](https://knowledge.hubspot.com/properties/hubspots-default-activity-properties). Company identifiers can associate activities; task imports require title and due date. Notes/tasks can be created but not updated by activity import. Portal permissions, mapping and record resolution remain untested. No integration or import is implemented.
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local work plan and explicit review ledger; no outreach or CRM access")
    parser.add_argument("command", choices=["build", "review"])
    parser.add_argument("--public", type=Path, default=ROOT / "data/public_organisers.json")
    parser.add_argument("--crm", type=Path, default=ROOT / "data/synthetic/baseline.json")
    parser.add_argument("--rules", type=Path, default=ROOT / "priority_engine/rules.json")
    parser.add_argument("--policy", type=Path, default=ROOT / "priority_engine/workplan_rules.json")
    parser.add_argument("--capacity", type=Path, default=ROOT / "data/synthetic/capacity.json")
    parser.add_argument("--reviews", type=Path, default=ROOT / "data/synthetic/reviews.json")
    parser.add_argument("--as-of")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/milestone2/baseline")
    parser.add_argument("--account")
    parser.add_argument("--fingerprint", help="Exact work snapshot fingerprint shown in unified_queue.csv")
    parser.add_argument("--decision", choices=["accept", "hold", "evidence-needed"])
    parser.add_argument("--reviewer")
    parser.add_argument("--reason")
    parser.add_argument("--at", help="Explicit review timestamp with offset")
    parser.add_argument("--question", help="Specific evidence requested; authorizes internal research only")
    parser.add_argument("--reopen-condition")
    parser.add_argument("--reopen-at")
    args = parser.parse_args(argv)
    try:
        core, contexts, issues = load_core(args)
        policy = load(args.policy)
        if args.command == "review":
            if not all([args.account, args.fingerprint, args.decision, args.reviewer, args.reason, args.at]):
                raise ValueError("Review requires account, fingerprint, decision, reviewer, reason and at")
            items = prepare_work(core, contexts, policy)
            item = next((i for i in items if i["account_id"] == args.account), None)
            if item is None or args.fingerprint != item["review_fingerprint"]:
                raise ValueError("Unknown account or changed snapshot; rebuild and review the current work")
            ledger = load(args.reviews) if args.reviews.exists() else {
                "metadata": {"synthetic": True, "label": "Human decisions about simulated work, not Humanitix approvals"}, "decisions": []}
            entry = decision_record(item, args.decision.replace("-", "_"), args.reviewer, args.reason, args.at,
                                    args.question, args.reopen_condition, args.reopen_at)
            candidate = {**ledger, "decisions": ledger["decisions"] if entry in ledger["decisions"] else ledger["decisions"] + [entry]}
            _, errors = resolve_reviews(candidate, items,
                                        timestamp(core["metadata"]["as_of"]))
            if errors:
                raise ValueError(errors[0]["reason"])
            args.reviews.parent.mkdir(parents=True, exist_ok=True)
            save_json(args.reviews, candidate)
            print(json.dumps({"review_id": entry["review_id"], "decision": entry["decision"], "saved_to": str(args.reviews)}))
            return 0
        ledger, capacity = load(args.reviews), load(args.capacity)
        plan = build_workplan(core, contexts, ledger, capacity, policy)
        plan["metadata"]["provenance"].update({"review_ledger_sha256": digest(args.reviews),
                                                "capacity_sha256": digest(args.capacity), "work_policy_sha256": digest(args.policy)})
        handoff = build_handoff(plan, contexts)
        write_plan(plan, handoff, core, issues, args.output)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps({"accounts": len(plan["work"]), "assigned": plan["metadata"]["assigned_tasks"],
                      "unassigned": plan["metadata"]["unassigned_tasks"], "assigned_minutes": plan["metadata"]["assigned_minutes"],
                      "review_issues": len(plan["review_issues"]), "output": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
