"""Milestone 2 tests, leaving all 55 Milestone 1 checks unchanged."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from priority_engine.handoff import build_handoff
from priority_engine.operations import build_workplan, decision_record, prepare_work
from tools.create_workplan_demo import make_demo
from test_engine import CLOCK, FIXTURES, PUBLIC, ROOT, evaluate, example
from tools.create_demo_crm import callback

POLICY = json.loads((ROOT / "priority_engine/workplan_rules.json").read_text())
CORE = evaluate()[0]
CONTEXTS = {r["account_id"]: r for r in FIXTURES["baseline"]["accounts"]}
LEDGER, CAPACITY = make_demo(CORE, CONTEXTS, POLICY)


def empty_ledger():
    return {"metadata": {"synthetic": True}, "decisions": []}


def small(aid="US035", change=None, decision="accept", **review_options):
    public, crm, account, context = example(aid)
    if change:
        change(account, context)
    core = evaluate(public, crm)[0]
    contexts = {context["account_id"]: context}
    item = prepare_work(core, contexts, POLICY)[0]
    ledger = empty_ledger()
    if decision:
        ledger["decisions"] = [decision_record(item, decision, "SIM_REVIEWER", "Explicit test decision", "2026-10-22T13:00:00Z", **review_options)]
    return core, contexts, ledger, item


def plan_small(aid="US035", change=None, decision="accept", capacity=None, **options):
    core, contexts, ledger, _ = small(aid, change, decision, **options)
    return build_workplan(core, contexts, ledger, capacity or CAPACITY, POLICY)


class UnifiedViewTests(unittest.TestCase):
    def test_all_95_retained_in_one_contiguous_rank(self):
        plan = build_workplan(CORE, CONTEXTS, LEDGER, CAPACITY, POLICY)
        self.assertEqual([i["unified_rank"] for i in plan["work"]], list(range(1, 96)))
        self.assertEqual({i["account_id"] for i in plan["work"]}, {a["account_id"] for a in PUBLIC["organisers"]})

    def test_original_classifications_and_commercial_results_preserved(self):
        plan = build_workplan(CORE, CONTEXTS, LEDGER, CAPACITY, POLICY)
        for item in plan["work"]:
            row = next(r for r in CORE["accounts"] if r["account_id"] == item["account_id"])
            self.assertEqual(item["underlying_queue"], row["action"]["queue"])
            self.assertEqual(item["underlying_rule_trace"], row["action"]["rule_trace"])
            self.assertEqual(item["commercial_potential"], row["commercial_potential"])
            self.assertEqual(item["qualification"], row["qualification"])

    def test_due_commitments_precede_cold_qualification(self):
        items = prepare_work(CORE, CONTEXTS, POLICY)
        ranks = {i["account_id"]: i["unified_rank"] for i in items}
        self.assertLess(ranks["US014"], ranks["US001"])
        self.assertLess(ranks["US008"], ranks["US001"])

    def test_work_kind_distinguishes_sales_research_qualification_and_compliance(self):
        items = {i["account_id"]: i for i in prepare_work(CORE, CONTEXTS, POLICY)}
        self.assertEqual(items["US008"]["work_kind"], "sales_preparation")
        self.assertEqual(items["US026"]["work_kind"], "internal_research")
        self.assertEqual(items["US001"]["work_kind"], "internal_qualification")
        self.assertEqual(items["US072"]["work_kind"], "compliance_review")

    def test_no_input_mutation(self):
        core, contexts, ledger, capacity = deepcopy((CORE, CONTEXTS, LEDGER, CAPACITY))
        before = deepcopy((core, contexts, ledger, capacity))
        build_workplan(core, contexts, ledger, capacity, POLICY)
        self.assertEqual((core, contexts, ledger, capacity), before)

    def test_input_order_does_not_change_plan(self):
        core, ledger, capacity = deepcopy(CORE), deepcopy(LEDGER), deepcopy(CAPACITY)
        core["accounts"].reverse()
        ledger["decisions"].reverse()
        capacity["representatives"].reverse()
        self.assertEqual(build_workplan(CORE, CONTEXTS, LEDGER, CAPACITY, POLICY), build_workplan(core, CONTEXTS, ledger, capacity, POLICY))

    def test_every_unassigned_item_explains_why(self):
        plan = build_workplan(CORE, CONTEXTS, LEDGER, CAPACITY, POLICY)
        for item in plan["work"]:
            self.assertTrue(item["allocation_reason"])
            if item["allocation_status"] == "unassigned":
                self.assertIsNone(item["assigned_rep_id"])
                self.assertIsNone(item["planned_start_at"])


class HumanReviewTests(unittest.TestCase):
    def test_no_explicit_decision_means_no_assignment(self):
        plan = build_workplan(CORE, CONTEXTS, empty_ledger(), CAPACITY, POLICY)
        self.assertEqual(plan["metadata"]["assigned_tasks"], 0)

    def test_crm_review_state_does_not_replace_explicit_ledger(self):
        plan = plan_small(change=lambda a,c: c.update(review_state="accepted_for_internal_task"), decision=None)
        self.assertEqual(plan["metadata"]["assigned_tasks"], 0)

    def test_accept_assigns_only_the_displayed_snapshot(self):
        plan = plan_small()
        self.assertEqual(plan["metadata"]["assigned_tasks"], 1)
        self.assertEqual(plan["work"][0]["review_state"], "accept")

    def test_hold_requires_reopen_condition_and_does_not_assign(self):
        bad = plan_small(decision="hold")
        self.assertTrue(bad["review_issues"])
        good = plan_small(decision="hold", reopen_condition="Buyer evidence reviewed")
        self.assertFalse(good["review_issues"])
        self.assertEqual(good["metadata"]["assigned_tasks"], 0)

    def test_expired_hold_never_auto_accepts(self):
        plan = plan_small(decision="hold", reopen_condition="Manual review", reopen_at="2026-10-22T13:30:00Z")
        self.assertEqual(plan["work"][0]["review_state"], "hold")
        self.assertEqual(plan["metadata"]["assigned_tasks"], 0)

    def test_evidence_needed_is_internal_research_not_sales_approval(self):
        core, contexts, ledger, _ = small("US008", change=lambda a,c: c["interactions"].append(
            {"interaction_id":"SIM_REPLY","occurred_at":"2026-10-21T14:00:00Z","channel":"email","outcome":"positive_reply","synthetic":True}),
            decision="evidence_needed", requested_evidence="Identify the actual ticketing decision owner")
        item = build_workplan(core, contexts, ledger, CAPACITY, POLICY)["work"][0]
        self.assertEqual(item["underlying_queue"], "response_review")
        self.assertEqual(item["work_kind"], "internal_research")
        self.assertEqual(item["execution_scope"], "internal_only")
        self.assertIn("actual ticketing decision owner", item["work_title"])

    def test_vague_evidence_decision_and_missing_reviewer_rejected(self):
        core, contexts, ledger, _ = small(decision="evidence_needed")
        self.assertTrue(build_workplan(core, contexts, ledger, CAPACITY, POLICY)["review_issues"])
        ledger["decisions"][0].update(decision="accept", reviewer_id="")
        self.assertEqual(build_workplan(core, contexts, ledger, CAPACITY, POLICY)["metadata"]["assigned_tasks"], 0)

    def test_later_hold_supersedes_accept(self):
        core, contexts, ledger, item = small()
        ledger["decisions"].append(decision_record(item, "hold", "SIM_R", "Wait", "2026-10-22T13:30:00Z", reopen_condition="Manual review"))
        plan = build_workplan(core, contexts, ledger, CAPACITY, POLICY)
        self.assertEqual(plan["work"][0]["review_state"], "hold")
        self.assertEqual(plan["metadata"]["assigned_tasks"], 0)

    def test_later_explicit_accept_releases_hold(self):
        core, contexts, ledger, item = small(decision="hold", reopen_condition="Manual review")
        ledger["decisions"].append(decision_record(item,"accept","SIM_R","Reviewed","2026-10-22T13:30:00Z"))
        self.assertEqual(build_workplan(core, contexts, ledger, CAPACITY, POLICY)["metadata"]["assigned_tasks"], 1)

    def test_same_time_conflicting_decisions_fail_closed(self):
        core, contexts, ledger, item = small()
        ledger["decisions"].append(decision_record(item,"hold","SIM_R","Wait","2026-10-22T13:00:00Z",reopen_condition="Review"))
        plan = build_workplan(core, contexts, ledger, CAPACITY, POLICY)
        self.assertTrue(plan["review_issues"])
        self.assertEqual(plan["metadata"]["assigned_tasks"], 0)

    def test_repeated_identical_decision_is_idempotent(self):
        core, contexts, ledger, _ = small()
        before = build_workplan(core, contexts, ledger, CAPACITY, POLICY)
        ledger["decisions"].append(deepcopy(ledger["decisions"][0]))
        self.assertEqual(before, build_workplan(core, contexts, ledger, CAPACITY, POLICY))

    def test_future_review_cannot_approve_work(self):
        core, contexts, ledger, _ = small()
        ledger["decisions"][0]["reviewed_at"] = "2026-10-23T13:00:00Z"
        self.assertEqual(build_workplan(core, contexts, ledger, CAPACITY, POLICY)["metadata"]["assigned_tasks"], 0)

    def test_changed_crm_snapshot_invalidates_approval(self):
        core, contexts, ledger, _ = small()
        contexts["US035"]["owner_id"] = "SIM_REP_B"
        plan = build_workplan(core, contexts, ledger, CAPACITY, POLICY)
        self.assertEqual(plan["work"][0]["review_state"], "stale_decision")
        self.assertEqual(plan["metadata"]["assigned_tasks"], 0)

    def test_changed_clock_or_duration_requires_re_review(self):
        core, contexts, ledger, _ = small()
        core["metadata"]["as_of"] = (CLOCK + timedelta(minutes=1)).isoformat()
        self.assertEqual(build_workplan(core, contexts, ledger, CAPACITY, POLICY)["work"][0]["review_state"], "stale_decision")
        policy = deepcopy(POLICY)
        policy["task_minutes"]["internal_qualification"] = 20
        self.assertEqual(build_workplan(core, contexts, ledger, CAPACITY, policy)["metadata"]["assigned_tasks"], 0)

    def test_changed_primary_check_date_requires_re_review(self):
        core, contexts, ledger, _ = small()
        core["accounts"][0]["public_rule_trace"][3]["checked_on"] = "2026-10-21"
        self.assertEqual(build_workplan(core, contexts, ledger, CAPACITY, POLICY)["work"][0]["review_state"], "stale_decision")

    def test_scenario_label_does_not_invalidate_unchanged_account_reviews(self):
        variant = evaluate(crm=FIXTURES["callback_due"])[0]
        contexts = {r["account_id"]: r for r in FIXTURES["callback_due"]["accounts"]}
        before = {i["account_id"]: i["review_fingerprint"] for i in prepare_work(CORE, CONTEXTS, POLICY)}
        after = {i["account_id"]: i["review_fingerprint"] for i in prepare_work(variant, contexts, POLICY)}
        self.assertEqual([aid for aid in before if before[aid] != after[aid]], ["US035"])

    def test_new_restriction_requires_re_review_and_never_reuses_old_acceptance(self):
        variant = evaluate(crm=FIXTURES["callback_restricted"])[0]
        contexts = {r["account_id"]: r for r in FIXTURES["callback_restricted"]["accounts"]}
        plan = build_workplan(variant, contexts, LEDGER, CAPACITY, POLICY)
        item = next(i for i in plan["work"] if i["account_id"] == "US035")
        self.assertEqual(item["underlying_queue"], "restricted")
        self.assertEqual(item["review_state"], "stale_decision")
        self.assertEqual(item["allocation_status"], "unassigned")
        self.assertFalse(item["outreach_authorized"])


class OwnershipCapacityAndRestrictionTests(unittest.TestCase):
    def test_zero_capacity_owner_keeps_account_unassigned(self):
        plan = plan_small(change=lambda a,c: c.update(owner_id="SIM_REP_D"))
        item = plan["work"][0]
        self.assertIsNone(item["assigned_rep_id"])
        self.assertIn("Ownership preserved", item["allocation_reason"])
        self.assertEqual(sum(r["assigned_minutes"] for r in plan["representatives"]), 0)

    def test_owned_task_never_moves_to_roomier_rep(self):
        plan = plan_small(change=lambda a,c: c.update(owner_id="SIM_REP_B"))
        self.assertEqual(plan["work"][0]["assigned_rep_id"], "SIM_REP_B")

    def test_unowned_uses_largest_remaining_capacity(self):
        capacity = deepcopy(CAPACITY)
        capacity["representatives"][-1]["available_minutes"] = 80
        item = plan_small(capacity=capacity)["work"][0]
        self.assertEqual(item["assigned_rep_id"], "SIM_REP_E")
        self.assertEqual(item["capacity_before_allocation"]["SIM_REP_E"], 80)
        self.assertEqual(item["capacity_before_allocation"]["SIM_REP_A"], 60)

    def test_capacity_tie_uses_stable_id_not_equal_rotation(self):
        self.assertEqual(plan_small()["work"][0]["assigned_rep_id"], "SIM_REP_A")

    def test_exact_fit_and_fragmented_time(self):
        capacity = deepcopy(CAPACITY)
        for r,n in zip(capacity["representatives"],[5,9,15,0,3]): r["available_minutes"] = n
        plan = plan_small(capacity=capacity)
        self.assertEqual(plan["work"][0]["assigned_rep_id"], "SIM_REP_C")
        self.assertEqual(plan["representatives"][2]["remaining_minutes"], 0)
        capacity["representatives"][2]["available_minutes"] = 10
        plan = plan_small(capacity=capacity)
        self.assertEqual(plan["metadata"]["assigned_tasks"], 0)
        self.assertIn("No representative", plan["work"][0]["allocation_reason"])

    def test_totals_never_exceed_available_minutes(self):
        plan = build_workplan(CORE, CONTEXTS, LEDGER, CAPACITY, POLICY)
        items = {i["work_key"]: i for i in plan["work"]}
        for r in plan["representatives"]:
            total = sum(items[k]["estimated_minutes"] for k in r["assignments"])
            self.assertEqual(total, r["assigned_minutes"])
            self.assertEqual(r["remaining_minutes"], r["available_minutes"]-total)
            self.assertGreaterEqual(r["remaining_minutes"], 0)
            ranks = [items[k]["unified_rank"] for k in r["assignments"]]
            self.assertEqual(ranks, sorted(ranks))

    def test_negative_capacity_or_duration_is_rejected(self):
        capacity = deepcopy(CAPACITY)
        capacity["representatives"][0]["available_minutes"] = -1
        with self.assertRaises(ValueError): plan_small(capacity=capacity)
        policy = deepcopy(POLICY)
        policy["task_minutes"]["internal_research"] = -10
        with self.assertRaises(ValueError): build_workplan(CORE, CONTEXTS, LEDGER, CAPACITY, policy)

    def test_roster_must_contain_exactly_five_fictional_reps(self):
        capacity = deepcopy(CAPACITY)
        capacity["representatives"].pop()
        with self.assertRaises(ValueError): plan_small(capacity=capacity)

    def test_human_accept_cannot_override_future_wait(self):
        plan = plan_small(change=lambda a,c: callback(c,48))
        self.assertEqual(plan["metadata"]["assigned_tasks"], 0)
        self.assertIn("Recorded wait", plan["work"][0]["allocation_reason"])

    def test_human_accept_cannot_revive_suppressed_acquisition(self):
        plan = plan_small(change=lambda a,c: c.update(restriction_state="global_do_not_contact"))
        self.assertEqual(plan["metadata"]["assigned_tasks"], 0)
        self.assertIn("Restriction", plan["work"][0]["allocation_reason"])

    def test_conflicting_callback_restriction_allows_only_internal_compliance(self):
        def change(a,c):
            callback(c,-1)
            c.update(restriction_state="channel_restricted", restricted_channels=["phone"])
        item = plan_small(change=change)["work"][0]
        self.assertEqual(item["underlying_queue"], "restricted")
        self.assertEqual(item["work_kind"], "compliance_review")
        self.assertEqual(item["execution_scope"], "internal_only")
        self.assertFalse(item["outreach_authorized"])

    def test_existing_customer_never_becomes_net_new_assignment(self):
        plan = plan_small(change=lambda a,c: c.update(simulated_relationship="customer"))
        self.assertEqual(plan["metadata"]["assigned_tasks"], 0)
        self.assertIn("outside this net-new", plan["work"][0]["allocation_reason"])

    def test_unverified_accounts_can_receive_approved_research(self):
        item = plan_small("US004")["work"][0]
        self.assertEqual(item["work_kind"], "internal_research")
        self.assertEqual(item["qualification"]["verification_status"], "not_reviewed")
        self.assertEqual(item["allocation_status"], "assigned")


class DraftAndCliTests(unittest.TestCase):
    def test_drafts_only_for_assigned_reviewed_work(self):
        plan = build_workplan(CORE, CONTEXTS, LEDGER, CAPACITY, POLICY)
        handoff = build_handoff(plan, CONTEXTS)
        expected = {i["account_id"] for i in plan["work"] if i["allocation_status"] == "assigned"}
        for kind in ("companies","notes","tasks"):
            self.assertEqual({r["account_id"] for r in handoff[kind]}, expected)
            self.assertEqual(len(handoff[kind]), len(expected))

    def test_draft_has_no_real_owner_ids_or_completed_sales_activities(self):
        plan = build_workplan(CORE, CONTEXTS, LEDGER, CAPACITY, POLICY)
        handoff = build_handoff(plan, CONTEXTS)
        for task in handoff["tasks"]:
            self.assertFalse(task["eligible_for_live_export"])
            self.assertIsNone(task["target_company_record_id"])
            self.assertIsNone(task["standard_properties"]["Activity assigned to"])
            self.assertEqual(task["standard_properties"]["Task type"], "To-Do")
            self.assertEqual(task["standard_properties"]["Task status"], "Not started")

    def test_notes_visibly_separate_public_claims_from_simulated_history(self):
        plan = build_workplan(CORE, CONTEXTS, LEDGER, CAPACITY, POLICY)
        for note in build_handoff(plan, CONTEXTS)["notes"]:
            body = note["standard_properties"]["Note body"]
            self.assertIn("PUBLIC RESEARCH", body)
            self.assertIn("SIMULATED CRM HISTORY", body)
            self.assertIn("REVIEW OF THE SIMULATED WORK", body)
            self.assertIn('"annual_paid_tickets": null', body)

    def test_existing_task_is_reconciled_not_duplicated_by_import(self):
        core,contexts,ledger,_ = small(change=lambda a,c: c.update(open_review_task_id="SIM_TASK_35"))
        plan = build_workplan(core,contexts,ledger,CAPACITY,POLICY)
        task = build_handoff(plan,contexts)["tasks"][0]
        self.assertEqual(task["simulated_existing_task_id"], "SIM_TASK_35")
        self.assertIn("Update existing task manually", task["operation"])

    def test_assignment_does_not_overwrite_company_ownership(self):
        core,contexts,ledger,_ = small()
        plan = build_workplan(core,contexts,ledger,CAPACITY,POLICY)
        company = build_handoff(plan,contexts)["companies"][0]
        self.assertNotIn("Company owner", company["standard_properties"])
        self.assertIsNone(company["simulated_existing_owner_id"])

    def test_original_milestone1_tested_code_and_tests_unchanged(self):
        original = json.loads((ROOT/"tests/fixtures/milestone1_test_results.json").read_text())
        for name,value in original["tested_file_sha256"].items():
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(), value, name)

    def test_cli_plan_outputs_repeat_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory)/name for name in ("a","b")]
            for path in paths:
                subprocess.run([sys.executable,"-m","priority_engine.workplan","build","--output",str(path)], cwd=ROOT,check=True,capture_output=True)
            self.assertEqual({p.name for p in paths[0].iterdir()}, {p.name for p in paths[1].iterdir()})
            for file in paths[0].iterdir(): self.assertEqual(file.read_bytes(), (paths[1]/file.name).read_bytes())

    def test_explicit_review_cli_is_idempotent_and_checks_snapshot(self):
        item = next(i for i in prepare_work(CORE,CONTEXTS,POLICY) if i["account_id"]=="US035")
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory)/"reviews.json"
            command = [sys.executable,"-m","priority_engine.workplan","review","--account","US035",
                       "--fingerprint",item["review_fingerprint"],"--decision","accept","--reviewer","SIM_TEST",
                       "--reason","Explicit local test","--at","2026-10-22T13:00:00Z","--reviews",str(ledger)]
            for _ in range(2): subprocess.run(command,cwd=ROOT,check=True,capture_output=True)
            self.assertEqual(len(json.loads(ledger.read_text())["decisions"]),1)
            bad = command.copy();bad[bad.index("--fingerprint")+1]="old_snapshot"
            self.assertNotEqual(subprocess.run(bad,cwd=ROOT,capture_output=True).returncode,0)
            self.assertEqual(len(json.loads(ledger.read_text())["decisions"]),1)

    def test_manual_conflicting_decision_does_not_corrupt_ledger(self):
        item = next(i for i in prepare_work(CORE,CONTEXTS,POLICY) if i["account_id"]=="US035")
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory)/"reviews.json"
            command=[sys.executable,"-m","priority_engine.workplan","review","--account","US035","--fingerprint",item["review_fingerprint"],
                     "--decision","accept","--reviewer","SIM_TEST","--reason","Accept","--at","2026-10-22T13:00:00Z","--reviews",str(ledger)]
            subprocess.run(command,cwd=ROOT,check=True,capture_output=True)
            before=ledger.read_bytes()
            command[command.index("--decision")+1]="hold"
            command += ["--reopen-condition","Review again"]
            self.assertNotEqual(subprocess.run(command,cwd=ROOT,capture_output=True).returncode,0)
            self.assertEqual(ledger.read_bytes(),before)


if __name__ == "__main__":
    unittest.main()
