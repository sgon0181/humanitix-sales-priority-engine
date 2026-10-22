"""Human-reviewed work above the unchanged Milestone 1 classifications."""
from collections import Counter
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

from .engine import SIGNALS
from .commercial import ordering as commercial_ordering
from .validation import timestamp


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def prepare_work(core, contexts, policy):
    """One next-work item per account. No allocation or approval occurs here."""
    validate_policy(policy)
    items = []
    for row in core["accounts"]:
        context = contexts.get(row["account_id"], {})
        action, qualification = row["action"], row["qualification"]
        queue = action["queue"]
        if queue in {"callback_review", "urgent_clearance_review"}:
            band, timing = "due_commitment", action["callback_due_at"]
        elif queue == "response_review":
            band, timing = "recent_reply", action["oldest_recent_reply_at"]
        else:
            band = {"qualification_review": "qualification", "evidence_research": "evidence_research",
                    "internal_clearance": "clearance", "restricted": "policy_review",
                    "contact_policy_review": "policy_review", "deferred": "waiting",
                    "existing_relationship": "existing_relationship"}[queue]
            timing = None
        if queue in {"restricted", "contact_policy_review", "internal_clearance", "urgent_clearance_review"}:
            kind = "compliance_review"
        elif queue == "qualification_review":
            kind = "internal_qualification"
        elif queue in {"callback_review", "response_review"} and qualification["identity_observed"]:
            kind = "sales_preparation"
        else:
            kind = "internal_research"
        # A restriction is never a reason to create busywork. Only a conflicting
        # requested callback offers a useful internal resolution task.
        restricted_conflict = queue == "restricted" and bool(action["callback_due_at"])
        blocked = None
        if queue == "existing_relationship":
            blocked = "Existing customer/opportunity; retain owner and route outside this net-new work plan"
        elif queue == "deferred":
            blocked = "Recorded wait/cooldown until " + str(action["reopen_at"])
        elif queue == "restricted" and not restricted_conflict:
            blocked = "Restriction is recorded; no acquisition work or redundant suppression task"
        title = action["recommended_action"]
        if kind == "sales_preparation":
            title = ("Prepare the requested callback and qualification questions; do not contact" if queue == "callback_review"
                     else "Prepare a response brief for the recent reply; do not send")
        item = {
            "work_key": "US_POC:" + row["account_id"] + ":next_work",
            "account_id": row["account_id"], "organisation_name": row["organisation_name"],
            "canonical_domain": row["canonical_domain"], "priority_band": band,
            "priority_timing": timing, "underlying_queue": queue,
            "underlying_queue_rank": row["queue_rank"], "underlying_rule": action["matched_rule"],
            "original_recommendation": action["recommended_action"],
            "work_kind": kind, "work_title": title,
            "execution_scope": "preparation_only_no_contact" if kind == "sales_preparation" else "internal_only",
            "estimated_minutes": policy["task_minutes"][kind],
            "existing_owner_id": action["existing_owner_id"],
            "existing_task_id": context.get("open_review_task_id"),
            "commercial_potential": deepcopy(row["commercial_potential"]),
            "qualification": deepcopy(qualification), "provenance": deepcopy(row["provenance"]),
            "public_sources": row["public_sources"], "underlying_rule_trace": deepcopy(action["rule_trace"]),
            "policy_block": blocked, "synthetic": True,
            "scenario_id": row["scenario_id"], "needs_human_review": True,
            "eligible_for_live_export": False, "outreach_authorized": False,
        }
        if "simulated_commercial" in row:
            item["simulated_commercial"] = deepcopy(row["simulated_commercial"])
        reviewed_state = {
            "as_of": core["metadata"]["as_of"], "policy": policy,
            "qualification": qualification, "commercial": row["commercial_potential"],
            "public_rule_trace": row["public_rule_trace"], "action": action,
            "crm_context": {k: v for k, v in context.items() if k != "scenario_id"},
        }
        if "simulated_commercial" in row:
            reviewed_state["simulated_commercial"] = row["simulated_commercial"]
        item["review_fingerprint"] = fingerprint(reviewed_state)
        items.append(item)
    def key(item):
        return (policy["priority_order"].index(item["priority_band"]),
                timestamp(item["priority_timing"]).timestamp() if item["priority_timing"] else 0,
                *commercial_ordering(item.get("simulated_commercial")),
                SIGNALS.index(item["commercial_potential"]["activity_signal"]),
                len(item["qualification"]["unresolved_checks"]), item["account_id"])
    items.sort(key=key)
    for rank, item in enumerate(items, 1):
        item["unified_rank"] = rank
        item["priority_explanation"] = {
            "band": item["priority_band"], "commitment_or_reply_time": item["priority_timing"],
            "activity_signal": item["commercial_potential"]["activity_signal"],
            "unresolved_public_checks": len(item["qualification"]["unresolved_checks"]),
            "tie_breaker": item["account_id"],
            "note": "Operational ordering, not a revenue forecast or permission to contact",
        }
        if "simulated_commercial" in item:
            item["priority_explanation"]["commercial_ordering"] = "Within the same action category and timing: known illustrative annual gross fee revenue descending, then switching difficulty low to high; unknown fees last. GMV bands are descriptive only."
    return items


def validate_policy(policy):
    kinds = {"sales_preparation", "internal_research", "internal_qualification", "compliance_review"}
    bands = {"due_commitment", "recent_reply", "qualification", "evidence_research",
             "clearance", "policy_review", "waiting", "existing_relationship"}
    if (not isinstance(policy, dict) or not policy.get("version")
            or not isinstance(policy.get("task_minutes"), dict)
            or set(policy.get("task_minutes", {})) != kinds
            or any(type(v) is not int or v <= 0 for v in policy["task_minutes"].values())
            or not isinstance(policy.get("priority_order"), list)
            or not all(isinstance(v, str) for v in policy["priority_order"])
            or len(policy["priority_order"]) != len(bands) or set(policy["priority_order"]) != bands):
        raise ValueError("Invalid work policy: positive durations and each supported priority band exactly once are required")


def decision_record(item, decision, reviewer_id, reason, reviewed_at,
                    requested_evidence=None, reopen_condition=None, reopen_at=None,
                    source="user_entered"):
    record = {
        "work_key": item["work_key"], "account_id": item["account_id"],
        "fingerprint": item["review_fingerprint"], "decision": decision,
        "reviewer_id": reviewer_id, "reason": reason, "reviewed_at": reviewed_at,
        "requested_evidence": requested_evidence, "reopen_condition": reopen_condition,
        "reopen_at": reopen_at, "synthetic": True, "source": source,
    }
    record["review_id"] = "REVIEW_" + fingerprint(record)[:24]
    return record


def resolve_reviews(ledger, items, clock):
    """Latest explicit decision wins; conflicting or invalid histories fail closed."""
    if (not isinstance(ledger, dict) or not isinstance(ledger.get("metadata"), dict)
            or ledger["metadata"].get("synthetic") is not True
            or not isinstance(ledger.get("decisions"), list)):
        raise ValueError("Review ledger must be labelled synthetic and contain decisions")
    lookup = {i["work_key"]: i for i in items}
    history, issues, invalid_keys, review_ids = {}, [], set(), {}
    for entry in ledger["decisions"]:
        key = entry.get("work_key") if isinstance(entry, dict) else None
        try:
            if key not in lookup or entry.get("account_id") != lookup[key]["account_id"]:
                raise ValueError("Unknown review target")
            if entry.get("synthetic") is not True or entry.get("decision") not in {"accept", "hold", "evidence_needed"}:
                raise ValueError("Invalid decision or missing synthetic label")
            if not all(isinstance(entry.get(k), str) and entry[k].strip() for k in
                       ("review_id", "reviewer_id", "reason", "fingerprint", "reviewed_at")):
                raise ValueError("Review requires ID, reviewer, reason, snapshot and time")
            if entry.get("source") not in {"user_entered", "synthetic_fixture"}:
                raise ValueError("Review source must be explicit")
            if timestamp(entry["reviewed_at"]) > clock:
                raise ValueError("Review time is after the plan clock")
            if entry["decision"] == "evidence_needed" and not str(entry.get("requested_evidence") or "").strip():
                raise ValueError("Evidence-needed decision requires a specific question")
            if entry["decision"] == "hold" and not str(entry.get("reopen_condition") or "").strip():
                raise ValueError("Hold requires a reopen condition")
            if entry.get("reopen_at") and timestamp(entry["reopen_at"]) < timestamp(entry["reviewed_at"]):
                raise ValueError("Reopen time cannot precede review")
            rid = entry["review_id"]
            if rid in review_ids:
                if review_ids[rid] == entry:
                    continue
                invalid_keys.add(review_ids[rid]["work_key"])
                raise ValueError("Conflicting duplicate review ID")
            review_ids[rid] = entry
            history.setdefault(key, []).append(entry)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            if isinstance(key, str):
                invalid_keys.add(key)
            issues.append({"work_key": key, "reason": str(exc)})
    resolved = {}
    for key, entries in history.items():
        if key in invalid_keys:
            continue
        entries.sort(key=lambda r: (timestamp(r["reviewed_at"]), r["review_id"]))
        latest = entries[-1]
        if sum(timestamp(r["reviewed_at"]) == timestamp(latest["reviewed_at"]) for r in entries) > 1:
            issues.append({"work_key": key, "reason": "Conflicting decisions at the same timestamp; re-review required"})
            continue
        resolved[key] = deepcopy(latest)
    return resolved, issues


def validate_capacity(capacity):
    if (not isinstance(capacity, dict) or not isinstance(capacity.get("metadata"), dict)
            or capacity["metadata"].get("synthetic") is not True
            or not isinstance(capacity.get("representatives"), list)):
        raise ValueError("Capacity must be a labelled synthetic roster")
    reps = capacity["representatives"]
    if (len(reps) != 5 or not all(isinstance(r, dict) and isinstance(r.get("rep_id"), str) for r in reps)
            or {r.get("rep_id") for r in reps} != {"SIM_REP_" + c for c in "ABCDE"}):
        raise ValueError("Exactly the five fictional representatives A to E are required")
    for rep in reps:
        if (type(rep.get("available_minutes")) is not int or rep["available_minutes"] < 0
                or not isinstance(rep.get("name"), str) or not rep["name"].strip()):
            raise ValueError("Each rep requires a name and nonnegative integer minutes")
    return {r["rep_id"]: {**deepcopy(r), "remaining_minutes": r["available_minutes"],
                           "assigned_minutes": 0, "assignments": []} for r in sorted(reps, key=lambda r: r["rep_id"])}


def build_workplan(core, contexts, ledger, capacity, policy, proposal_only=False, input_blocks=None):
    clock = timestamp(core["metadata"]["as_of"])
    items = prepare_work(core, contexts, policy)
    reviews, issues = resolve_reviews(ledger, items, clock)
    invalid_review_keys = {i.get("work_key") for i in issues if isinstance(i.get("work_key"), str)}
    reps = validate_capacity(capacity)
    for item in items:
        review = reviews.get(item["work_key"])
        item.update(review=review, review_state="unreviewed", assigned_rep_id=None,
                    rep_sequence=None, planned_start_at=None, planned_end_at=None,
                    allocation_status="unassigned", allocation_reason=None, capacity_before_allocation=None)
        reason = item["policy_block"] or (input_blocks or {}).get(item["account_id"])
        if proposal_only and item["work_key"] in invalid_review_keys:
            reason = "Resolve invalid or conflicting review history before proposing work"
        if not review:
            if not proposal_only:
                reason = reason or "No valid explicit human decision for this work snapshot"
        elif review["fingerprint"] != item["review_fingerprint"]:
            item["review_state"] = "stale_decision"
            if not proposal_only:
                reason = reason or "Public evidence, CRM context, clock or work policy changed; re-review required"
            elif review["decision"] == "hold":
                reason = "Existing human hold must be resolved before proposing work"
        else:
            item["review_state"] = review["decision"]
            if review["decision"] == "hold":
                reason = "Human hold: " + review["reason"] + "; reopen: " + review["reopen_condition"]
            elif review["decision"] == "evidence_needed":
                # This decision approves only the specific bounded research request.
                # It never approves the original sales follow-up or clears restrictions.
                if item["underlying_queue"] in {"restricted", "contact_policy_review", "internal_clearance", "urgent_clearance_review"}:
                    item["work_kind"] = "compliance_review"
                else:
                    item["work_kind"] = "internal_research"
                item["execution_scope"] = "internal_only"
                item["work_title"] = "Research requested evidence: " + review["requested_evidence"]
                item["estimated_minutes"] = policy["task_minutes"][item["work_kind"]]
        if reason:
            item["allocation_reason"] = reason
            continue
        owner = item["existing_owner_id"]
        minutes = item["estimated_minutes"]
        item["capacity_before_allocation"] = {rid: r["remaining_minutes"] for rid, r in reps.items()}
        if owner:
            rep = reps.get(owner)
            if rep is None:
                item["allocation_reason"] = "Existing owner is not in the available roster; ownership preserved"
                continue
            if rep["remaining_minutes"] < minutes:
                item["allocation_reason"] = f"Existing owner {owner} has {rep['remaining_minutes']} minutes; task needs {minutes}. Ownership preserved"
                continue
            assignment_reason = "Existing owner preserved; approved work fits remaining capacity"
        else:
            fits = [r for r in reps.values() if r["remaining_minutes"] >= minutes]
            if not fits:
                item["allocation_reason"] = f"No representative has the {minutes} minutes needed for this approved unowned task"
                continue
            rep = min(fits, key=lambda r: (-r["remaining_minutes"], r["rep_id"]))
            assignment_reason = "Unowned work: most remaining minutes among fitting reps; stable rep ID breaks ties"
        item.update(assigned_rep_id=rep["rep_id"], rep_sequence=len(rep["assignments"]) + 1,
                    planned_start_at=(clock + timedelta(minutes=rep["assigned_minutes"])).isoformat(),
                    planned_end_at=(clock + timedelta(minutes=rep["assigned_minutes"] + minutes)).isoformat(),
                    allocation_status="assigned", allocation_reason=assignment_reason)
        rep["remaining_minutes"] -= minutes
        rep["assigned_minutes"] += minutes
        rep["assignments"].append(item["work_key"])
    result = {
        "metadata": {"version": policy["version"], "as_of": core["metadata"]["as_of"],
                     "scenario_id": core["metadata"]["scenario_id"], "synthetic": True,
                     "label": "SIMULATED CRM, REVIEW DECISIONS AND CAPACITY. REAL PUBLIC IDENTITIES. NO OUTREACH.",
                     "accounts": len(items), "assigned_tasks": sum(i["allocation_status"] == "assigned" for i in items),
                     "unassigned_tasks": sum(i["allocation_status"] == "unassigned" for i in items),
                     "assigned_minutes": sum(r["assigned_minutes"] for r in reps.values()),
                     "decision_counts": dict(sorted(Counter(i["review_state"] for i in items).items())),
                     "priority_order": policy["priority_order"], "assumptions": policy,
                     "provenance": deepcopy(core["metadata"].get("provenance", {})),
                     "limitations": "Sales activities are preparation only. No contact is authorized. Global rank includes blocked/held work with explicit reasons, not a call list."},
        "representatives": list(reps.values()), "work": items, "review_issues": issues,
    }
    if proposal_only:
        result["metadata"].update(proposal_only=True, label="WORKLOAD PROPOSAL ONLY. NO APPROVALS CREATED. NO OUTREACH.",
                                  proposed_tasks=result["metadata"]["assigned_tasks"],
                                  proposed_minutes=result["metadata"]["assigned_minutes"], assigned_tasks=0, assigned_minutes=0)
        for item in items:
            item["proposed_rep_id"] = item["assigned_rep_id"]
            item["assigned_rep_id"] = None
            item["proposal_only"] = True
            item["allocation_reason"] = item["allocation_reason"].replace("approved unowned task", "proposed unowned task")
            if item["allocation_status"] == "assigned":
                item["allocation_status"] = "proposed"
                item["allocation_reason"] = "Proposal, subject to human review: " + item["allocation_reason"].replace("approved work", "proposed work")
        for rep in result["representatives"]:
            rep["proposed_minutes"] = rep["assigned_minutes"]
            rep["assigned_minutes"] = 0
            rep["proposed_work"] = rep["assignments"]
            rep["assignments"] = []
        result["metadata"]["unassigned_tasks"] = len(items)
    return result
