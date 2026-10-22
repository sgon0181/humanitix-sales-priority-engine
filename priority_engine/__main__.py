"""Run with python3 -m priority_engine. Writes local review outputs only."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .engine import ACTIVE_QUEUES, OTHER_QUEUES, build_queue
from .validation import timestamp, validate_crm, validate_public

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def md(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def write_outputs(result, report, output):
    output.mkdir(parents=True, exist_ok=True)
    save_json(output / "queue.json", result)
    save_json(output / "validation_report.json", report)
    fields = ["account_id", "organisation_name", "queue", "queue_rank", "activity_signal",
              "urgency", "matched_rule", "recommended_action", "verification_status",
              "annual_paid_tickets", "annual_ticket_value_usd", "callback_due_at", "reopen_at",
              "existing_owner_id", "synthetic", "scenario_id", "context_status",
              "eligible_for_live_export", "source_record_ids", "public_sources", "reason_fields"]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in result["accounts"]:
        action, potential = row["action"], row["commercial_potential"]
        writer.writerow({
            **{k: row[k] for k in ("account_id", "organisation_name", "queue_rank", "synthetic", "scenario_id", "context_status")},
            **{k: action[k] for k in ("queue", "urgency", "matched_rule", "recommended_action", "callback_due_at", "reopen_at", "existing_owner_id", "eligible_for_live_export")},
            **{k: potential[k] for k in ("activity_signal", "annual_paid_tickets", "annual_ticket_value_usd")},
            "verification_status": row["qualification"]["verification_status"],
            "source_record_ids": "; ".join(row["provenance"]["source_record_ids"]),
            "public_sources": "; ".join(row["public_sources"]),
            "reason_fields": "; ".join(action["rule_trace"][-1]["fields"]),
        })
    (output / "queue.csv").write_text(stream.getvalue(), encoding="utf-8", newline="")
    meta = result["metadata"]
    lines = ["# Generated internal priority queues", "", meta["label"], "",
             f"Scenario: {meta['scenario_id']}. Clock: {meta['as_of']}. Rules: {meta['rules_version']}.", "",
             f"Accounts: {meta['account_count']}. Public evidence unreviewed: {meta['unverified_count']}.", "",
             "Annual paid volume and revenue remain unknown. Ranks apply within each internal work queue only.", "",
             "No contact, allocation, HubSpot import or live export is authorized by this output.", ""]
    for queue in ACTIVE_QUEUES + OTHER_QUEUES:
        items = [r for r in result["accounts"] if r["action"]["queue"] == queue]
        if not items:
            continue
        lines += ["## " + queue.replace("_", " ").title(), "",
                  "| Queue rank | ID | Organiser | Commercial activity signal | Rule | Recommended internal action |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for row in items:
            lines.append("| " + " | ".join(md(v) for v in [row["queue_rank"] or "Unranked", row["account_id"], row["organisation_name"],
                row["commercial_potential"]["activity_signal"], row["action"]["matched_rule"], row["action"]["recommended_action"]]) + " |")
        lines.append("")
    lines += ["## Explanation and provenance", "",
              "queue.json contains each supporting offer path, reviewed URL, unresolved check, evaluated action rule and sorting basis. Original research URLs remain labelled unverified.", "",
              "validation_report.json records rejected fields and input hashes. Missing evidence is a research state, not a validation failure.", ""]
    (output / "queue.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local Humanitix POC: internal review only; CRM context is simulated")
    parser.add_argument("--public", type=Path, default=ROOT / "data/public_organisers.json")
    parser.add_argument("--crm", type=Path, default=ROOT / "data/synthetic/baseline.json")
    parser.add_argument("--rules", type=Path, default=ROOT / "priority_engine/rules.json")
    parser.add_argument("--as-of", help="ISO timestamp with UTC offset; default is the fixed fixture clock")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/milestone1/baseline")
    args = parser.parse_args(argv)
    try:
        public_doc, crm_doc, rules = load(args.public), load(args.crm), load(args.rules)
        clock = timestamp(args.as_of or crm_doc["metadata"]["scenario_clock"])
        ZoneInfo(rules["business_timezone"])
        for field in ("evidence_freshness_days", "offer_window_days", "cooldown_days", "attempt_window_days", "attempt_limit", "reply_window_days"):
            if type(rules[field]) is not int or rules[field] < 1:
                raise ValueError("Rule setting must be a positive integer: " + field)
        accounts, public_issues = validate_public(public_doc)
        crm, crm_issues = validate_crm(crm_doc, {a["account_id"] for a in accounts}, clock)
        scenario = crm_doc["metadata"]["scenario_id"]
        result = build_queue(accounts, crm, clock, rules, scenario)
        manifest = {
            "public_input_sha256": digest(args.public), "crm_input_sha256": digest(args.crm),
            "rules_sha256": digest(args.rules),
            "original_raw_sha256": public_doc.get("metadata", {}).get("source_sha256"),
        }
        result["metadata"]["provenance"] = manifest
        report = {"status": "passed" if not public_issues and not crm_issues else "completed_with_issues",
                  "accepted_public_accounts": len(accounts), "accepted_crm_contexts": len(crm),
                  "issues": public_issues + crm_issues, "provenance": manifest,
                  "note": "Unverified public claims are retained as research work, not rejected. Invalid CRM context becomes unknown."}
        write_outputs(result, report, args.output)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps({"status": report["status"], "accounts": len(accounts),
                      "unverified": result["metadata"]["unverified_count"],
                      "queues": result["metadata"]["queue_counts"], "output": str(args.output)}, indent=2))
    return 0 if accounts else 1


if __name__ == "__main__":
    raise SystemExit(main())
