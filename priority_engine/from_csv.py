"""One CSV -> validated evidence/CRM -> reviewed work plan and handoff drafts."""
import argparse
import csv
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

from .__main__ import ROOT, digest, load, save_json
from .commercial import FIELDS as COMMERCIAL_FIELDS, assess as assess_commercial, attach as attach_commercial, load_settings
from .engine import build_queue
from .handoff import build_handoff
from .operations import build_workplan, prepare_work
from .validation import has_decision_inputs, timestamp, url_ok, validate_crm, validate_public
from .workplan import write_csv, write_plan

CLAIMS = ["city", "state", "event_category", "event_frequency", "published_ticket_prices_usd",
          "observed_ticketing_platform", "ticketing_authority_evidence", "potential_switching_barriers", "uncertainty_notes"]
COLUMNS = ["account_id", "organisation_name", "website", "as_of", "crm_context_origin",
           *CLAIMS, "source_urls_json", "source_record_ids_json", "public_evidence_json",
           "evidence_reviewed_by", "evidence_checked_on", "evidence_access_basis", "identity_observed", "us_scope",
           "public_source_url", "event_name", "event_start_date", "ticket_price_usd", "purchase_unit",
           "owner_id", "crm_match_state", "crm_relationship", "restriction_state", "restricted_channels",
           "last_interaction_at", "last_interaction_outcome", "last_interaction_channel", "interactions_json",
           "callback_requested_at", "callback_due_at", "callback_channel", "callback_status", "defer_until", "existing_task_id",
           "review_decision", "review_fingerprint", "reviewer_id", "review_reason", "reviewed_at", "review_origin",
           "requested_evidence", "reopen_condition", "reopen_at", *COMMERCIAL_FIELDS]


def clean(value):
    if isinstance(value, str):
        return "".join(", " if (unicodedata.category(c) == "Pd" and c != "-") or c == "\u2212" else c for c in value)
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    return value


def json_cell(row, name, default, expected):
    if not row.get(name):
        return default
    value = json.loads(row[name])
    if not isinstance(value, expected):
        raise ValueError(name + " has the wrong JSON type")
    if has_decision_inputs(value):
        raise ValueError("Predetermined decisions are forbidden in evidence/history JSON")
    return value


def public_record(row, number, checksum):
    aid, website = row["account_id"], row["website"]
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", aid):
        raise ValueError("account_id must be a nonempty ASCII identifier")
    if not row["organisation_name"] or not url_ok(website):
        raise ValueError("Name and http/https website are required")
    urls = json_cell(row, "source_urls_json", [], list)
    ids = json_cell(row, "source_record_ids_json", [], list)
    if not all(isinstance(v, str) and v for v in ids):
        raise ValueError("source_record_ids_json must contain nonempty strings")
    ev = json_cell(row, "public_evidence_json", None, dict)
    if ev is not None:
        if row.get("evidence_reviewed_by") or row.get("event_name") or row.get("ticket_price_usd"):
            raise ValueError("Use either public_evidence_json or flat evidence columns, not both")
        if ev.get("account_id") != aid:
            raise ValueError("Evidence account_id must match its CSV row")
        if not isinstance(ev.get("us_scope"), str) or not isinstance(ev.get("source_urls"), list):
            raise ValueError("Rich evidence needs typed us_scope and source_urls")
        for key in ("offer_observations", "provider_observations", "dependency_observations"):
            if not isinstance(ev.get(key, []), list):
                raise ValueError("Rich evidence field must be an array: " + key)
    elif row.get("evidence_reviewed_by"):
        date.fromisoformat(row["evidence_checked_on"])
        source = row["public_source_url"]
        if not url_ok(source) or row["evidence_access_basis"] not in {"direct_primary", "primary_search_index_only"}:
            raise ValueError("Reviewed flat evidence needs a source URL and explicit access basis")
        if row["identity_observed"] not in {"true", "false"}:
            raise ValueError("Reviewed identity_observed must be true or false")
        ev = {"account_id": aid, "verification_status": "field_reviewed", "checked_on": row["evidence_checked_on"],
              "access_basis": row["evidence_access_basis"], "reviewed_by": row["evidence_reviewed_by"],
              "source_urls": [source], "us_scope": row["us_scope"] or "unknown",
              "offer_observations": [], "provider_observations": [], "dependency_observations": [],
              "unresolved": ["annual_paid_ticket_volume", "buying_authority", "switching_intent", "real_crm_clearance"]}
        if row.get("ticket_price_usd"):
            ev["offer_observations"] = [{
                "name": row["event_name"], "event_start_date": row["event_start_date"] or None,
                "price_min": float(row["ticket_price_usd"]), "price_max": float(row["ticket_price_usd"]),
                "currency": "USD", "price_basis": "published_offer", "unit": row["purchase_unit"],
                "availability": "not_checked", "fees": "not_separated", "source_url": source,
                "access_basis": row["evidence_access_basis"], "observed_on": row["evidence_checked_on"]}]
    if ev and ev.get("access_basis") == "primary_search_index_only":
        identity = "primary_index_identity_observed"
    elif (ev and ev.get("access_basis") in {"direct_primary", "direct_checkout_and_primary_index", "direct_homepage_and_primary_index"}
          and (not row.get("evidence_reviewed_by") or row["identity_observed"] == "true")):
        identity = "primary_identity_observed"
    else:
        identity = "imported_identity_unverified"
    if ev:
        urls += ev.get("source_urls", [])
    # Plain prices, frequency and dates stay imported claims without a declared review.
    claims = {k: row.get(k) or None for k in CLAIMS}
    if not ev:
        claims["unreviewed_flat_evidence"] = {k: row.get(k) or None for k in
            ["event_name", "event_start_date", "ticket_price_usd", "purchase_unit", "public_source_url", "us_scope"]}
    return clean({
        "account_id": aid, "organisation_name": row["organisation_name"], "website": website,
        "canonical_domain": urlsplit(website).hostname.lower().removeprefix("www."),
        "identity_status": identity, "verification_status": "field_reviewed" if ev else "not_reviewed",
        "source_record_ids": ids + [f"csv:{checksum}:row:{number}"], "input_row": number,
        "source_urls": sorted(set(urls)), "imported_claims": claims, "public_evidence": ev,
        "real_crm_clearance": "not_available", "evidence_origin": "CSV-provided observations; not verified by the importer",
    })


def crm_record(row):
    origin = row["crm_context_origin"]
    if origin not in {"synthetic", "provided_unverified"}:
        raise ValueError("crm_context_origin must explicitly be synthetic or provided_unverified")
    aid = row["account_id"]
    events = json_cell(row, "interactions_json", [], list)
    if events and any(row.get(k) for k in ["last_interaction_at", "last_interaction_outcome", "last_interaction_channel"]):
        raise ValueError("Use either interactions_json or last-interaction columns")
    if any(row.get(k) for k in ["last_interaction_at", "last_interaction_outcome", "last_interaction_channel"]):
        if not all(row.get(k) for k in ["last_interaction_at", "last_interaction_outcome", "last_interaction_channel"]):
            raise ValueError("Last interaction requires time, outcome and channel")
        events.append({"interaction_id": "CSV_LAST_" + aid, "occurred_at": row["last_interaction_at"],
                       "outcome": row["last_interaction_outcome"], "channel": row["last_interaction_channel"], "synthetic": True})
    callback = None
    if any(row.get(k) for k in ["callback_due_at", "callback_requested_at", "callback_channel", "callback_status"]):
        if not all(row.get(k) for k in ["callback_due_at", "callback_requested_at", "callback_channel"]):
            raise ValueError("Callback requires requested time, due time and channel")
        matching = [e for e in events if e.get("outcome") == "callback_requested"
                    and timestamp(e["occurred_at"]) == timestamp(row["callback_requested_at"])]
        if len(matching) > 1:
            raise ValueError("Ambiguous callback request history")
        request_id = matching[0]["interaction_id"] if matching else "CSV_CALLBACK_" + aid
        if not matching:
            events.append({"interaction_id": request_id, "occurred_at": row["callback_requested_at"],
                           "outcome": "callback_requested", "channel": row["callback_channel"], "synthetic": True})
        callback = {"requested_at": row["callback_requested_at"], "due_at": row["callback_due_at"],
                    "channel": row["callback_channel"], "status": row["callback_status"] or "open",
                    "source_interaction_id": request_id, "synthetic": True}
    return clean({"account_id": aid, "scenario_id": "CSV_PREVIEW", "synthetic": True,
                  "source_origin": origin,
                  "simulated_match_state": row["crm_match_state"] or "unknown",
                  "simulated_relationship": row["crm_relationship"] or "unknown",
                  "restriction_state": row["restriction_state"] or "unknown",
                  "restricted_channels": [c.strip() for c in row["restricted_channels"].split(";") if c.strip()],
                  "owner_id": row["owner_id"] or None, "interactions": events, "callback": callback,
                  "defer_until": row["defer_until"] or None, "open_review_task_id": row["existing_task_id"] or None})


def read_csv(path, as_of=None, commercial_settings=None):
    commercial_settings = load_settings(commercial_settings)
    raw = Path(path).read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
    fields = reader.fieldnames or []
    if len(fields) != len(set(fields)):
        raise ValueError("Duplicate CSV headers")
    unknown = set(fields) - set(COLUMNS)
    if unknown:
        raise ValueError("Unsupported columns (including priorities/scores): " + ", ".join(sorted(unknown)))
    if not {"account_id", "organisation_name", "website"} <= set(fields):
        raise ValueError("CSV requires account_id, organisation_name and website")
    records = list(reader)
    if not records:
        raise ValueError("CSV contains no organiser rows")
    clocks = {r.get("as_of", "").strip() for r in records if r.get("as_of", "").strip()}
    if len(clocks) > 1:
        raise ValueError("CSV contains conflicting as_of clocks")
    clock_text = as_of or next(iter(clocks), None)
    if not clock_text:
        raise ValueError("Provide one as_of value in the CSV or --as-of; there is no hidden demo clock")
    clock = timestamp(clock_text)
    public, contexts, decisions, claims, issues, commercial = [], [], [], [], [], []
    for number, raw_row in enumerate(records, 2):
        aid = raw_row.get("account_id")
        claims.append({"input_row": number, "account_id": aid, "original_values": clean(raw_row)})
        if None in raw_row or any(v is None for v in raw_row.values()):
            issues.append({"account_id": aid, "input_row": number, "severity": "rejected_account", "reason": "CSV row width differs from header"})
            continue
        row = {k: raw_row.get(k, "").strip() for k in COLUMNS}
        try:
            public.append(public_record(row, number, checksum))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            issues.append({"account_id": aid, "input_row": number, "severity": "rejected_account", "reason": str(exc)})
            continue
        if any(row.get(field) for field in COMMERCIAL_FIELDS):
            assessment = {"account_id": row["account_id"], "input_row": number, **assess_commercial(row, commercial_settings)}
            commercial.append(assessment)
            if assessment["invalid_fields"]:
                issues.append({"account_id": aid, "input_row": number, "severity": "invalid_commercial_assumption",
                               "reason": "Invalid simulated inputs remain Unknown: " + ", ".join(assessment["invalid_fields"])})
        try:
            contexts.append(crm_record(row))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            issues.append({"account_id": aid, "input_row": number, "severity": "rejected_crm_context", "reason": str(exc) + "; use unknown context"})
        if row["review_decision"]:
            # Preserve the supplied snapshot. Never generate/rebind an approval.
            decisions.append(clean({"work_key": "US_POC:" + row["account_id"] + ":next_work", "account_id": row["account_id"],
                              "fingerprint": row["review_fingerprint"], "decision": row["review_decision"].replace("-", "_"),
                              "reviewer_id": row["reviewer_id"], "reason": row["review_reason"], "reviewed_at": row["reviewed_at"],
                              "source": row["review_origin"], "review_id": f"CSV_REVIEW_{number}", "synthetic": True,
                              "requested_evidence": row["requested_evidence"] or None,
                              "reopen_condition": row["reopen_condition"] or None, "reopen_at": row["reopen_at"] or None}))
    return {"public": {"organisers": public}, "crm": {"metadata": {"synthetic": True, "scenario_id": "CSV_PREVIEW", "scenario_clock": clock.isoformat()}, "accounts": contexts},
            "reviews": {"metadata": {"synthetic": True}, "decisions": decisions}, "clock": clock, "commercial": commercial,
            "source_claims": claims, "issues": issues, "input_sha256": checksum, "input_row_count": len(records),
            "commercial_settings": commercial_settings}


def execute(path, output, capacity_path, rules_path, policy_path, as_of=None, commercial_settings=None):
    if Path(path).resolve().parent == Path(output).resolve():
        raise ValueError("Keep the input CSV in a separate directory from generated outputs")
    imported = read_csv(path, as_of, commercial_settings)
    accounts, pi = validate_public(imported["public"])
    if not accounts:
        raise ValueError("No valid organiser accounts; inspect required identity fields")
    contexts, ci = validate_crm(imported["crm"], {a["account_id"] for a in accounts}, imported["clock"])
    rules, policy = load(rules_path), load(policy_path)
    for field in ("evidence_freshness_days", "offer_window_days", "cooldown_days", "attempt_window_days", "attempt_limit", "reply_window_days"):
        if type(rules[field]) is not int or rules[field] < 1:
            raise ValueError("Rule setting must be a positive integer: " + field)
    core = build_queue(accounts, contexts, imported["clock"], rules, "CSV_PREVIEW")
    attach_commercial(core, imported["commercial"])
    core["metadata"]["provenance"] = {"csv_input_sha256": imported["input_sha256"],
                                         "rules_sha256": digest(rules_path), "work_policy_sha256": digest(policy_path),
                                         "capacity_sha256": digest(capacity_path)}
    input_blocks = {i["account_id"]: "Resolve CSV CRM validation: " + i["reason"] for i in imported["issues"] + ci
                    if i.get("severity") == "rejected_crm_context"}
    plan = build_workplan(core, contexts, imported["reviews"], load(capacity_path), policy, input_blocks=input_blocks)
    proposal = build_workplan(core, contexts, imported["reviews"], load(capacity_path), policy,
                              proposal_only=True, input_blocks=input_blocks)
    label = "CSV INPUT PREVIEW. PUBLIC OBSERVATIONS AS LABELLED; CRM ORIGIN EXPLICIT. FICTIONAL CAPACITY. NO OUTREACH."
    core["metadata"]["label"] = label
    plan["metadata"].update(label=label, input_row_count=imported["input_row_count"],
                             crm_origins=sorted({c["source_origin"] for c in contexts.values()}))
    for item in plan["work"]:
        item["crm_source_origin"] = contexts.get(item["account_id"], {}).get("source_origin", "unavailable")
    handoff = build_handoff(plan, contexts)
    proposed_handoff = build_handoff(proposal, contexts, proposal_only=True)
    handoff["metadata"]["label"] = "CSV-BASED REVIEW DRAFT. DO NOT IMPORT. NO LIVE HUBSPOT INTEGRATION."
    for draft in (handoff, proposed_handoff):
        for note in draft["notes"]:
            if contexts.get(note["account_id"], {}).get("source_origin") == "provided_unverified":
                note["standard_properties"]["Note body"] = note["standard_properties"]["Note body"].replace(
                    "SIMULATED CRM HISTORY (not real organiser interactions):",
                    "PROVIDED CRM INFORMATION (unverified imported claims, not validated Humanitix facts):")
    output = Path(output)
    write_plan(plan, handoff, core, imported["issues"] + pi + ci, output)
    save_json(output / "workload_proposal.json", proposal)
    save_json(output / "handoff_PROPOSED.json", proposed_handoff)
    for kind in ("companies", "notes", "tasks"):
        rows = [{"Local draft key": r["draft_key"], "Company domain name": r["association_company_domain"],
                 **r["standard_properties"], "Pending human review": True, "Eligible for live export": False}
                for r in proposed_handoff[kind]]
        standard = {"companies": ["Name", "Company domain name"], "notes": ["Note body", "Activity date"],
                    "tasks": ["Task title", "Due date", "Task type", "Task status", "Activity assigned to", "Notes"]}[kind]
        headers = list(dict.fromkeys(["Local draft key", "Company domain name"] + standard
                                     + ["Pending human review", "Eligible for live export"]))
        write_csv(output / (kind + "_PROPOSED.csv"), rows, headers)
    proposal_columns = ["unified_rank", "account_id", "organisation_name", "underlying_queue", "work_kind", "review_state",
                        "estimated_minutes", "existing_owner_id", "proposed_rep_id", "allocation_status", "allocation_reason", "proposal_only", "simulated_commercial"]
    write_csv(output / "workload_proposal.csv", proposal["work"], proposal_columns)
    write_csv(output / "proposed_representatives.csv", proposal["representatives"],
              ["rep_id", "name", "available_minutes", "proposed_minutes", "remaining_minutes"])
    lines = ["# CSV workload proposal", "", "PROPOSAL ONLY. NO APPROVALS CREATED. NO OUTREACH. CAPACITY IS FICTIONAL.", "",
             "This view is generated even without review decisions. It preserves holds, ownership, restrictions and waits. Actual approved work remains in workplan.md; this proposal never grants approval.", "",
             "| Rep | Available minutes | Proposed minutes | Remaining after proposal |",
             "| --- | ---: | ---: | ---: |"]
    for rep in proposal["representatives"]:
        lines.append(f"| {rep['name']} | {rep['available_minutes']} | {rep['proposed_minutes']} | {rep['remaining_minutes']} |")
    lines += ["", "| Rank | Organiser | Work kind | Proposed rep | Minutes | Decision reason |",
              "| --- | --- | --- | --- | ---: | --- |"]
    for item in proposal["work"]:
        values = [item["unified_rank"], item["account_id"] + ": " + item["organisation_name"], item["work_kind"],
                  item["proposed_rep_id"] or "Not allocated", item["estimated_minutes"], item["allocation_reason"]]
        lines.append("| " + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in values) + " |")
    lines += ["", "handoff_PROPOSED.json contains pending-review company/note/task drafts. No actual owner IDs or live-export eligibility are supplied.", ""]
    (output / "workload_proposal.md").write_text("\n".join(lines), encoding="utf-8")
    # Keep originals and sectioned normalized inputs, never mutate project research.
    (output / "input.csv").write_bytes(Path(path).read_bytes())
    save_json(output / "imported_public.json", imported["public"])
    save_json(output / "imported_crm.json", imported["crm"])
    save_json(output / "simulated_commercial.json", {"metadata": {"synthetic": True, "settings": imported["commercial_settings"]}, "accounts": imported["commercial"]})
    save_json(output / "imported_reviews.json", imported["reviews"])
    save_json(output / "source_claims.json", {"input_sha256": imported["input_sha256"], "rows": imported["source_claims"]})
    save_json(output / "import_summary.json", {
        "input_sha256": imported["input_sha256"], "input_rows": imported["input_row_count"],
        "accepted_accounts": len(accounts), "accepted_crm_contexts": len(contexts),
        "unverified_accounts": core["metadata"]["unverified_count"], "assigned_tasks": plan["metadata"]["assigned_tasks"],
        "proposed_tasks": proposal["metadata"]["proposed_tasks"], "proposed_handoff_bundles": len(proposed_handoff["tasks"]),
        "handoff_bundles": len(handoff["tasks"]), "import_issues": len(imported["issues"] + pi + ci),
        "review_issues": len(plan["review_issues"]), "label": label,
        "truth_boundary": "No public source or CRM claim is independently verified by importing it. Preview flags do not grant real clearance."})
    return plan, handoff


def main(argv=None):
    parser = argparse.ArgumentParser(description="One CSV -> prioritised work plan and HubSpot-compatible review drafts")
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--as-of")
    parser.add_argument("--capacity", type=Path, default=ROOT / "data/synthetic/capacity.json")
    parser.add_argument("--rules", type=Path, default=ROOT / "priority_engine/rules.json")
    parser.add_argument("--policy", type=Path, default=ROOT / "priority_engine/workplan_rules.json")
    args = parser.parse_args(argv)
    stem = args.csv_file.stem if args.csv_file.stem not in {".", "..", ""} else "csv_batch"
    output = args.output or ROOT / "outputs/csv_runs" / stem
    try:
        # Prevent accidentally overwriting the supplied original with its archive copy.
        if args.csv_file.resolve() == (output / "input.csv").resolve():
            raise ValueError("Choose an output directory separate from the supplied input.csv")
        plan, handoff = execute(args.csv_file, output, args.capacity, args.rules, args.policy, args.as_of)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps({"accounts": len(plan["work"]), "assigned_tasks": plan["metadata"]["assigned_tasks"],
                      "assigned_minutes": plan["metadata"]["assigned_minutes"], "handoff_bundles": len(handoff["tasks"]),
                      "proposed_tasks": load(output / "import_summary.json")["proposed_tasks"],
                      "proposed_handoff_bundles": load(output / "import_summary.json")["proposed_handoff_bundles"],
                      "output": str(output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
