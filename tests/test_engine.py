"""Business counterexamples, invariants and full-dataset integration checks."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from priority_engine.engine import build_queue
from priority_engine.validation import validate_crm, validate_public
from tools.create_demo_crm import CLOCK, callback, event, make_fixtures

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = json.loads((ROOT / "data/public_organisers.json").read_text())
RULES = json.loads((ROOT / "priority_engine/rules.json").read_text())
FIXTURES = make_fixtures(PUBLIC)


def evaluate(public=PUBLIC, crm=None, clock=CLOCK, rules=RULES):
    crm = FIXTURES["baseline"] if crm is None else crm
    accounts, pissues = validate_public(public)
    contexts, cissues = validate_crm(crm, {a["account_id"] for a in accounts}, clock)
    result = build_queue(accounts, contexts, clock, rules, crm["metadata"]["scenario_id"])
    return result, pissues + cissues


def by_id(result, aid):
    return next(r for r in result["accounts"] if r["account_id"] == aid)


def example(aid="US035"):
    public = {"organisers": [deepcopy(next(a for a in PUBLIC["organisers"] if a["account_id"] == aid))]}
    context = deepcopy(next(a for a in FIXTURES["baseline"]["accounts"] if a["account_id"] == "US001"))
    context.update(account_id=aid, interactions=[], callback=None, owner_id=None)
    crm = {"metadata": deepcopy(FIXTURES["baseline"]["metadata"]), "accounts": [context]}
    return public, crm, public["organisers"][0], context


class PublicEvidenceTests(unittest.TestCase):
    def test_all_95_survive_including_unverified(self):
        result, issues = evaluate()
        self.assertEqual(issues, [])
        self.assertEqual(result["metadata"]["account_count"], 95)
        self.assertEqual(result["metadata"]["unverified_count"], 75)
        self.assertEqual({r["account_id"] for r in result["accounts"]}, {r["account_id"] for r in PUBLIC["organisers"]})

    def test_unverified_high_prices_and_frequencies_do_not_become_evidence(self):
        public, crm, account, _ = example("US004")
        account["imported_claims"].update(published_ticket_prices_usd="USD 10000", event_frequency="1000 per day")
        result, issues = evaluate(public, crm)
        row = result["accounts"][0]
        self.assertFalse(issues)
        self.assertEqual(row["commercial_potential"]["activity_signal"], "paid_activity_unestablished")
        self.assertEqual(row["action"]["matched_rule"], "A8")
        self.assertIn("Verify organiser identity", row["action"]["recommended_action"])

    def test_free_dates_do_not_create_paid_recurrence(self):
        public, crm, account, _ = example()
        offers = account["public_evidence"]["offer_observations"]
        account["public_evidence"]["offer_observations"] = [offers[0], offers[1], offers[3]]
        row = evaluate(public, crm)[0]["accounts"][0]
        self.assertEqual(row["commercial_potential"]["activity_signal"], "one_dated_paid_offer")
        self.assertEqual(len(row["commercial_potential"]["free_or_non_admission_paths"]), 2)

    def test_free_only_is_unknown_potential_not_zero(self):
        public, crm, account, _ = example()
        account["public_evidence"]["offer_observations"] = [o for o in account["public_evidence"]["offer_observations"] if o["price_min"] == 0]
        row = evaluate(public, crm)[0]["accounts"][0]
        self.assertEqual(row["commercial_potential"]["activity_signal"], "paid_activity_unestablished")
        self.assertIsNone(row["commercial_potential"]["annual_paid_tickets"])

    def test_festival_tiers_and_days_do_not_create_recurrence(self):
        public, crm, account, _ = example("US008")
        original = account["public_evidence"]["offer_observations"][0]
        second_day = deepcopy(original)
        second_day["event_start_date"] = "2027-04-02"
        account["public_evidence"]["offer_observations"].append(second_day)
        row = evaluate(public, crm)[0]["accounts"][0]
        self.assertEqual(row["commercial_potential"]["activity_signal"], "one_dated_paid_offer")

    def test_one_package_across_sessions_is_one_purchase(self):
        public, crm, account, _ = example("US049")
        package = account["public_evidence"]["offer_observations"][-1]
        second = deepcopy(package)
        second["event_start_date"] = "2026-11-13"
        account["public_evidence"]["offer_observations"] = [package, second]
        self.assertEqual(evaluate(public, crm)[0]["accounts"][0]["commercial_potential"]["activity_signal"], "one_dated_paid_offer")

    def test_same_date_modes_and_price_tiers_count_once(self):
        public, crm, _, _ = example("US069")
        row = evaluate(public, crm)[0]["accounts"][0]
        self.assertEqual(len(row["commercial_potential"]["distinct_paid_start_dates"]), 1)

    def test_high_ticket_price_does_not_change_signal_or_priority(self):
        public, crm, account, _ = example()
        before = evaluate(public, crm)[0]
        for o in account["public_evidence"]["offer_observations"]:
            if o["price_min"] > 0:
                o.update(price_min=10000, price_max=10000)
        self.assertEqual(before, evaluate(public, crm)[0])

    def test_historical_conference_needs_refresh(self):
        row = by_id(evaluate()[0], "US095")
        self.assertEqual(row["commercial_potential"]["activity_signal"], "undated_or_historical_paid_evidence")
        self.assertEqual(row["action"]["queue"], "evidence_research")

    def test_indexed_only_evidence_cannot_qualify_current_offer(self):
        public, crm, account, _ = example("US003")
        offer = account["public_evidence"]["offer_observations"][0]
        offer.update(event_start_date="2026-11-01", availability="not_checked")
        row = evaluate(public, crm)[0]["accounts"][0]
        self.assertFalse(row["qualification"]["current_paid_offer_observed"])

    def test_ambiguous_year_is_not_inferred(self):
        row = by_id(evaluate()[0], "US010")
        self.assertEqual(row["commercial_potential"]["activity_signal"], "undated_or_historical_paid_evidence")
        self.assertFalse(row["commercial_potential"]["distinct_paid_start_dates"])

    def test_stale_evidence_and_past_dates_recomputed(self):
        row = by_id(evaluate(clock=CLOCK + timedelta(days=100))[0], "US001")
        self.assertFalse(row["qualification"]["evidence_fresh"])
        self.assertFalse(row["qualification"]["current_paid_offer_observed"])
        self.assertEqual(row["action"]["queue"], "evidence_research")

    def test_source_date_in_future_cannot_support_current_signal(self):
        public, crm, account, _ = example()
        account["public_evidence"]["checked_on"] = "2026-10-23"
        self.assertFalse(evaluate(public, crm)[0]["accounts"][0]["qualification"]["evidence_fresh"])

    def test_sold_out_future_offer_keeps_signal_but_needs_availability_review(self):
        public, crm, account, _ = example("US008")
        for offer in account["public_evidence"]["offer_observations"]:
            offer["availability"] = "sold_out"
        row = evaluate(public, crm)[0]["accounts"][0]
        self.assertEqual(row["commercial_potential"]["activity_signal"], "one_dated_paid_offer")
        self.assertEqual(row["action"]["matched_rule"], "A8")

    def test_online_us_operation_requires_audience_scope_review(self):
        public, crm, _, _ = example("US049")
        row = evaluate(public, crm)[0]["accounts"][0]
        self.assertEqual(row["commercial_potential"]["activity_signal"], "repeated_dated_paid_offers")
        self.assertEqual(row["action"]["matched_rule"], "A8")
        self.assertIn("US event/sales scope", row["action"]["recommended_action"])

    def test_capacity_and_event_self_report_never_become_annual_volume(self):
        public = deepcopy(PUBLIC)
        for account in public["organisers"]:
            if account["public_evidence"]:
                account["public_evidence"]["capacity_observation"] = {"value": 9999999}
        result, _ = evaluate(public)
        for row in result["accounts"]:
            self.assertIsNone(row["commercial_potential"]["annual_paid_tickets"])
            self.assertIsNone(row["commercial_potential"]["annual_fee_potential_usd"])

    def test_configured_freshness_boundary_is_explicit(self):
        rules = {**RULES, "evidence_freshness_days": 1}
        public, crm, _, _ = example()
        self.assertTrue(evaluate(public, crm, CLOCK + timedelta(days=1), rules)[0]["accounts"][0]["qualification"]["evidence_fresh"])
        self.assertFalse(evaluate(public, crm, CLOCK + timedelta(days=2), rules)[0]["accounts"][0]["qualification"]["evidence_fresh"])

    def test_moth_dependency_kept_without_assuming_central_authority(self):
        row = by_id(evaluate()[0], "US080")
        self.assertTrue(any("Venue" in x["fact"] for x in row["qualification"]["dependencies"]))
        self.assertIsNone(row["commercial_potential"]["annual_paid_tickets"])


class CrmActionTests(unittest.TestCase):
    def test_counterfactual_changes_action_only(self):
        rows = [by_id(evaluate(crm=FIXTURES[v])[0], "US035") for v in ("baseline", "callback_due", "callback_restricted")]
        self.assertEqual([r["action"]["matched_rule"] for r in rows], ["A9", "A5", "A1"])
        self.assertEqual(rows[0]["commercial_potential"], rows[1]["commercial_potential"])
        self.assertEqual(rows[1]["commercial_potential"], rows[2]["commercial_potential"])

    def test_global_restriction_overrides_callback(self):
        public, crm, _, context = example()
        callback(context, -1)
        context["restriction_state"] = "global_do_not_contact"
        self.assertEqual(evaluate(public, crm)[0]["accounts"][0]["action"]["matched_rule"], "A1")

    def test_channel_restriction_does_not_substitute_email(self):
        public, crm, _, context = example()
        callback(context, -1)
        context.update(restriction_state="channel_restricted", restricted_channels=["phone"])
        row = evaluate(public, crm)[0]["accounts"][0]
        self.assertEqual(row["action"]["queue"], "restricted")
        self.assertIn("do not contact or substitute", row["action"]["recommended_action"])

    def test_unknown_clearance_with_due_callback_gets_urgent_internal_check(self):
        public, crm, _, context = example("US004")
        callback(context, -1)
        context["restriction_state"] = "unknown"
        row = evaluate(public, crm)[0]["accounts"][0]
        self.assertEqual(row["action"]["queue"], "urgent_clearance_review")
        self.assertFalse(row["action"]["outreach_authorized"])

    def test_unverified_account_can_have_urgent_internal_callback_review(self):
        public, crm, _, context = example("US004")
        callback(context, -1)
        row = evaluate(public, crm)[0]["accounts"][0]
        self.assertEqual(row["action"]["matched_rule"], "A5")
        self.assertEqual(row["qualification"]["verification_status"], "not_reviewed")
        self.assertTrue(row["qualification"]["unresolved_checks"])

    def test_existing_relationship_overrides_due_callback(self):
        for relationship in ("customer", "open_opportunity"):
            public, crm, _, context = example()
            callback(context, -1)
            context.update(simulated_relationship=relationship, owner_id="SIM_REP_D")
            row = evaluate(public, crm)[0]["accounts"][0]
            self.assertEqual(row["action"]["matched_rule"], "A2")
            self.assertEqual(row["action"]["existing_owner_id"], "SIM_REP_D")
            self.assertIsNone(row["queue_rank"])

    def test_future_callback_waits_even_with_positive_reply(self):
        public, crm, _, context = example()
        callback(context, 48)
        event(context, "positive_reply", 1, "email")
        self.assertEqual(evaluate(public, crm)[0]["accounts"][0]["action"]["matched_rule"], "A4")

    def test_later_explicit_defer_date_wins(self):
        public, crm, _, context = example()
        callback(context, 48)
        context["defer_until"] = (CLOCK + timedelta(days=5)).isoformat()
        self.assertEqual(evaluate(public, crm)[0]["accounts"][0]["action"]["reopen_at"], context["defer_until"])

    def test_recent_unanswered_attempt_does_not_become_rejection(self):
        public, crm, _, context = example()
        event(context, "no_answer", 2)
        action = evaluate(public, crm)[0]["accounts"][0]["action"]
        self.assertEqual(action["queue"], "deferred")
        self.assertEqual(action["reopen_at"], (CLOCK + timedelta(days=5)).isoformat())

    def test_attempt_limit_counts_whole_window(self):
        public, crm, _, context = example()
        for days in (25, 20, 10):
            event(context, "no_answer", days)
        action = evaluate(public, crm)[0]["accounts"][0]["action"]
        self.assertEqual(action["queue"], "deferred")
        self.assertEqual(action["reopen_at"], (CLOCK + timedelta(days=5)).isoformat())

    def test_cooldown_exact_expiry_allows_internal_qualification(self):
        public, crm, _, context = example()
        event(context, "no_answer", 7)
        self.assertEqual(evaluate(public, crm)[0]["accounts"][0]["action"]["matched_rule"], "A9")

    def test_requested_callback_overrides_generic_attempt_cooldown(self):
        public, crm, _, context = example()
        event(context, "no_answer", 1)
        callback(context, -1)
        self.assertEqual(evaluate(public, crm)[0]["accounts"][0]["action"]["matched_rule"], "A5")

    def test_recent_positive_reply_not_annual_value(self):
        public, crm, _, context = example()
        before = evaluate(public, crm)[0]["accounts"][0]
        event(context, "positive_reply", 1, "email")
        after = evaluate(public, crm)[0]["accounts"][0]
        self.assertEqual(after["action"]["matched_rule"], "A6")
        self.assertEqual(before["commercial_potential"], after["commercial_potential"])

    def test_expired_reply_does_not_create_urgency(self):
        public, crm, _, context = example()
        event(context, "positive_reply", 31, "email")
        self.assertEqual(evaluate(public, crm)[0]["accounts"][0]["action"]["matched_rule"], "A9")

    def test_decline_supersedes_earlier_callback(self):
        public, crm, _, context = example()
        callback(context, -1)
        event(context, "not_interested", 1)
        self.assertEqual(evaluate(public, crm)[0]["accounts"][0]["action"]["queue"], "contact_policy_review")

    def test_later_requested_callback_can_reopen_simple_decline(self):
        public, crm, _, context = example()
        event(context, "not_interested", 10)
        callback(context, -1)
        self.assertEqual(evaluate(public, crm)[0]["accounts"][0]["action"]["matched_rule"], "A5")

    def test_completed_callback_does_not_remain_due(self):
        public, crm, _, context = example()
        callback(context, -1)
        context["callback"]["status"] = "completed"
        self.assertEqual(evaluate(public, crm)[0]["accounts"][0]["action"]["matched_rule"], "A9")

    def test_simulated_accepted_review_never_upgrades_evidence_or_export(self):
        public, crm, _, context = example("US004")
        context["review_state"] = "accepted_for_internal_task"
        row = evaluate(public, crm)[0]["accounts"][0]
        self.assertEqual(row["qualification"]["verification_status"], "not_reviewed")
        self.assertFalse(row["action"]["eligible_for_live_export"])


class IntegrityAndValidationTests(unittest.TestCase):
    def test_every_output_is_labelled_and_internal_only(self):
        result, _ = evaluate()
        for row in result["accounts"]:
            self.assertTrue(row["synthetic"])
            self.assertTrue(row["action"]["needs_human_review"])
            self.assertFalse(row["action"]["eligible_for_live_export"])
            self.assertFalse(row["action"]["outreach_authorized"])

    def test_row_order_does_not_change_decisions(self):
        public, crm = deepcopy(PUBLIC), deepcopy(FIXTURES["baseline"])
        random.Random(42).shuffle(public["organisers"])
        random.Random(43).shuffle(crm["accounts"])
        self.assertEqual(evaluate()[0], evaluate(public, crm)[0])

    def test_provider_count_reference_flag_and_category_do_not_change_decisions(self):
        public = deepcopy(PUBLIC)
        for a in public["organisers"]:
            a.update(source_dataset_count=999, reference_case=not a["reference_case"])
            a["imported_claims"]["event_category"] = "Arbitrary category"
        self.assertEqual(evaluate()[0], evaluate(public)[0])

    def test_all_95_crm_changes_leave_public_commercial_assessment_unchanged(self):
        variants = [evaluate(crm=FIXTURES[v])[0] for v in FIXTURES]
        for row in variants[0]["accounts"]:
            for variant in variants[1:]:
                self.assertEqual(row["commercial_potential"], by_id(variant, row["account_id"])["commercial_potential"])

    def test_duplicate_ids_rejected_without_overwriting(self):
        public = deepcopy(PUBLIC)
        public["organisers"].append(deepcopy(public["organisers"][0]))
        accounts, issues = validate_public(public)
        self.assertEqual(len(accounts), 94)
        self.assertEqual(len(issues), 2)

    def test_invalid_offer_quarantined_without_losing_account(self):
        public, crm, account, _ = example()
        account["public_evidence"]["offer_observations"][0]["price_min"] = "999"
        result, issues = evaluate(public, crm)
        self.assertEqual(len(result["accounts"]), 1)
        self.assertEqual(issues[0]["severity"], "quarantined_offer")
        self.assertTrue(result["accounts"][0]["qualification"]["unresolved_checks"])

    def test_nonfinite_negative_and_reversed_prices_quarantined(self):
        for low, high in ((float("nan"), 20), (-1, 20), (20, 10), (True, 20)):
            public, crm, account, _ = example()
            account["public_evidence"]["offer_observations"][0].update(price_min=low, price_max=high)
            self.assertTrue(evaluate(public, crm)[1])

    def test_predetermined_priorities_rejected(self):
        public, crm, account, context = example()
        account["priority"] = "high"
        self.assertFalse(validate_public(public)[0])
        context["score"] = 100
        self.assertFalse(validate_crm(crm, {"US035"}, CLOCK)[0])

    def test_missing_and_invalid_crm_becomes_unknown(self):
        public, crm, _, context = example()
        context["restriction_state"] = "cleared_for_sales"
        result, issues = evaluate(public, crm)
        self.assertTrue(issues)
        self.assertEqual(result["accounts"][0]["action"]["queue"], "internal_clearance")
        crm["accounts"] = []
        self.assertEqual(evaluate(public, crm)[0]["accounts"][0]["action"]["queue"], "internal_clearance")

    def test_future_interactions_and_unlinked_callbacks_rejected(self):
        public, crm, _, context = example()
        event(context, "positive_reply", -1, "email")
        self.assertTrue(evaluate(public, crm)[1])
        context["interactions"] = []
        callback(context, -1)
        context["callback"]["source_interaction_id"] = "missing"
        self.assertTrue(evaluate(public, crm)[1])

    def test_unlabelled_crm_batch_is_rejected(self):
        crm = deepcopy(FIXTURES["baseline"])
        crm["metadata"]["synthetic"] = False
        with self.assertRaises(ValueError):
            evaluate(crm=crm)

    def test_pure_functions_do_not_mutate_inputs(self):
        public, crm = deepcopy(PUBLIC), deepcopy(FIXTURES["baseline"])
        before = deepcopy((public, crm))
        evaluate(public, crm)
        self.assertEqual((public, crm), before)

    def test_source_ids_still_join_original_provider_records(self):
        claims = json.loads((ROOT / "data/source_claims.json").read_text())
        valid = {s["source_record_id"] for s in claims["source_records"]}
        for row in evaluate()[0]["accounts"]:
            self.assertTrue(set(row["provenance"]["source_record_ids"]) <= valid)

    def test_raw_source_hash_unchanged(self):
        raw = ROOT / "data/raw/Humanitix_merged_research_unverified.csv"
        self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(), PUBLIC["metadata"]["source_sha256"])

    def test_ownership_preserved_and_no_allocation_output(self):
        result = evaluate()[0]
        for row in result["accounts"]:
            original = next(r for r in FIXTURES["baseline"]["accounts"] if r["account_id"] == row["account_id"])
            self.assertEqual(row["action"]["existing_owner_id"], original["owner_id"])
            self.assertNotIn("assigned_rep_id", row)

    def test_rank_only_within_active_queue_and_unique_recommendation_keys(self):
        result, _ = evaluate()
        keys = [r["recommendation_key"] for r in result["accounts"]]
        self.assertEqual(len(keys), len(set(keys)))
        for row in result["accounts"]:
            if row["action"]["queue"] in {"restricted", "deferred", "existing_relationship", "contact_policy_review"}:
                self.assertIsNone(row["queue_rank"])

    def test_callback_timing_precedes_public_signal(self):
        public = {"organisers": [deepcopy(PUBLIC["organisers"][0]), deepcopy(PUBLIC["organisers"][3])]}
        crm = {"metadata": deepcopy(FIXTURES["baseline"]["metadata"]), "accounts": []}
        for aid, hours in (("US001", -1), ("US004", -5)):
            _, _, _, context = example(aid)
            callback(context, hours)
            crm["accounts"].append(context)
        rows = evaluate(public, crm)[0]["accounts"]
        self.assertEqual([r["account_id"] for r in rows], ["US004", "US001"])

    def test_equivalent_clock_offsets_use_same_business_date_and_sorting(self):
        a = evaluate()[0]
        b = evaluate(clock=CLOCK.astimezone(timezone(timedelta(hours=12))))[0]
        self.assertEqual(a["accounts"], b["accounts"])

    def test_cli_outputs_are_byte_reproducible(self):
        with tempfile.TemporaryDirectory() as folder:
            outputs = [Path(folder)/name for name in ("a", "b")]
            for output in outputs:
                subprocess.run([sys.executable, "-m", "priority_engine", "--output", str(output)], cwd=ROOT, check=True, capture_output=True)
            self.assertEqual({p.name for p in outputs[0].iterdir()}, {"queue.json", "queue.csv", "queue.md", "validation_report.json"})
            for path in outputs[0].iterdir():
                self.assertEqual(path.read_bytes(), (outputs[1]/path.name).read_bytes())


if __name__ == "__main__":
    unittest.main()
