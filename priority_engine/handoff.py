"""Review-only company/note/task drafts. No network and no import operation."""
import json


def build_handoff(plan, contexts, proposal_only=False):
    companies, notes, tasks = [], [], []
    for item in plan["work"]:
        if item["allocation_status"] != ("proposed" if proposal_only else "assigned"):
            continue
        aid, context = item["account_id"], contexts.get(item["account_id"], {})
        common = {"account_id": aid, "work_key": item["work_key"], "synthetic": True,
                  "eligible_for_live_export": False, "target_company_record_id": None,
                  "association_company_domain": item["canonical_domain"]}
        if proposal_only:
            common.update(proposal_only=True, pending_human_review=True)
        companies.append({**common, "draft_key": "COMPANY:" + aid,
                          "standard_properties": {"Name": item["organisation_name"], "Company domain name": item["canonical_domain"]},
                          "ownership_rule": "Preserve existing company owner; do not assign or overwrite company ownership",
                          "simulated_existing_owner_id": item["existing_owner_id"],
                          "operation": "Resolve identity and existing CRM company before any create/update"})
        public = {"verification_status": item["qualification"]["verification_status"],
                  "commercial_activity_signal": item["commercial_potential"]["activity_signal"],
                  "annual_paid_tickets": None, "annual_fee_potential_usd": None,
                  "unresolved_public_checks": item["qualification"]["unresolved_checks"],
                  "dependencies": item["qualification"]["dependencies"],
                  "provider_roles": item["qualification"]["provider_observations"],
                  "reviewed_source_urls": item["public_sources"],
                  "original_research_urls_unverified": item["provenance"]["original_research_urls_unverified"],
                  "source_record_ids": item["provenance"]["source_record_ids"]}
        body = ("SIMULATION ONLY. NOT A REAL HUMANITIX CRM RECORD. NO CONTACT AUTHORIZED.\n\n"
                + "PUBLIC RESEARCH (partial field observations, unknown annual value):\n" + json.dumps(public, ensure_ascii=False, sort_keys=True)
                + "\n\nSIMULATED CRM HISTORY (not real organiser interactions):\n" + json.dumps(context, ensure_ascii=False, sort_keys=True)
                + "\n\nREVIEW OF THE SIMULATED WORK:\n" + json.dumps(item["review"], ensure_ascii=False, sort_keys=True)
                + f"\n\nDECISION: unified rank {item['unified_rank']}; underlying {item['underlying_queue']} / {item['underlying_rule']}. "
                + item["work_title"] + "\nAllocation: " + item["allocation_reason"])
        if proposal_only:
            body = "PROPOSAL PENDING HUMAN REVIEW. NO APPROVALS CREATED.\n\n" + body
        if "simulated_commercial" in item:
            body += "\n\nSIMULATED COMMERCIAL ASSUMPTIONS (not public evidence or real sales):\n" + json.dumps(item["simulated_commercial"], ensure_ascii=False, sort_keys=True)
        note_key = "NOTE:" + aid + ":US_POC"
        notes.append({**common, "draft_key": note_key,
                      "standard_properties": {"Note body": body, "Activity date": plan["metadata"]["as_of"]},
                      "operation": "Reconcile existing POC note before create; local key is not a HubSpot import upsert"})
        tasks.append({**common, "draft_key": "TASK:" + item["work_key"],
                      "simulated_existing_task_id": item["existing_task_id"],
                      "operation": "Update existing task manually after actual-ID resolution" if item["existing_task_id"] else "Check for an existing POC task before creating",
                      "simulated_assignee_id": item["proposed_rep_id"] if proposal_only else item["assigned_rep_id"],
                      "work_kind": item["work_kind"], "execution_scope": item["execution_scope"],
                      "estimated_minutes": item["estimated_minutes"],
                      "original_callback_due_at": context.get("callback", {}).get("due_at") if context.get("callback") else None,
                      "standard_properties": {
                          "Task title": "[SIMULATION] " + item["work_title"] + " (" + aid + ")",
                          "Due date": item["planned_end_at"], "Task type": "To-Do", "Task status": "Not started",
                          "Activity assigned to": None,
                          "Notes": "SIMULATED WORK. No outreach. Evidence note key: " + note_key
                                   + "; work key: " + item["work_key"] + "; " + item["allocation_reason"],
                      }})
    result = {"metadata": {"synthetic": True, "eligible_for_live_export": False,
                         "label": "REVIEW DRAFT ONLY. DO NOT IMPORT. NO LIVE HUBSPOT INTEGRATION.",
                         "scenario_id": plan["metadata"]["scenario_id"], "as_of": plan["metadata"]["as_of"],
                         "blocking_reasons": ["Simulated CRM, reviewers and capacity", "No real CRM clearance",
                                              "Actual company/task/owner IDs unresolved", "No authorized portal mapping or import test"],
                         "source_provenance": plan["metadata"]["provenance"]},
            "companies": companies, "notes": notes, "tasks": tasks}
    if proposal_only:
        result["metadata"].update(proposal_only=True, pending_human_review=True,
                                  label="PROPOSED HANDOFF. PENDING HUMAN REVIEW. DO NOT IMPORT.")
        result["metadata"]["blocking_reasons"].append("Human approval of the workload proposal is required")
    return result
