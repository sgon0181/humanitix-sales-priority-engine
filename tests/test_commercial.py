"""Commercial value changes ordering, never the underlying permission to act."""
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from priority_engine.__main__ import ROOT
from priority_engine.commercial import FIELDS, assess, ordering, load_settings
from priority_engine.from_csv import COLUMNS, execute
from priority_engine.demo_server import present, results_csv


def numbers(events="10", tickets="100", price="100", difficulty="medium"):
    return dict(zip(FIELDS, [events, tickets, price, difficulty]))


def organiser(aid, **changes):
    return {"account_id": aid, "organisation_name": "Simulated test context " + aid,
            "website": "https://" + aid.lower() + ".example/", "as_of": "2026-10-22T14:00:00Z",
            "crm_context_origin": "synthetic", "crm_match_state": "matched", "crm_relationship": "prospect",
            "restriction_state": "no_restriction_in_scenario", **numbers(), **changes}


class CommercialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "outputs")
        self.root = Path(self.temp.name)
        self.run_number = 0
        self.addCleanup(self.temp.cleanup)

    def run_rows(self, rows, commercial_settings=None):
        self.run_number += 1
        folder = self.root / str(self.run_number)
        folder.mkdir()
        source = folder / "batch.csv"
        with source.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        output = folder / "results"
        plan, handoff = execute(source, output, ROOT / "data/synthetic/capacity.json",
                                ROOT / "priority_engine/rules.json", ROOT / "priority_engine/workplan_rules.json",
                                commercial_settings=commercial_settings)
        return plan, handoff, output

    def test_formula_thresholds_and_zero_are_explicit(self):
        for price, amount, band in [("250", 250000, "High"), ("50", 50000, "Medium"),
                                    ("49.99", 49990, "Low"), ("0", 0, "Low")]:
            with self.subTest(price=price):
                result = assess(numbers(price=price))
                self.assertEqual(result["annual_paid_tickets"], 1000)
                self.assertEqual(result["annual_ticketing_potential_usd"], amount)
                self.assertEqual(result["opportunity"], band)
                self.assertEqual(result["origin"], "synthetic")
                self.assertIn("Not real organiser sales", result["label"])

    def test_missing_or_invalid_numerical_input_is_unknown_not_zero(self):
        for field in FIELDS[:3]:
            for value in ["", "NaN", "Infinity", "-1", "not researched"]:
                with self.subTest(field=field, value=value):
                    result = assess({**numbers(), field: value})
                    self.assertIsNone(result["annual_ticketing_potential_usd"])
                    self.assertIsNone(result["illustrative_annual_gross_fee_revenue_usd"])
                    self.assertEqual(result["opportunity"], "Unknown")
        self.assertEqual(assess(numbers(events="2.5"))["opportunity"], "Unknown")

    def test_switching_is_a_tie_break_not_an_invented_probability(self):
        low, high = assess(numbers(difficulty="low")), assess(numbers(difficulty="high"))
        self.assertEqual(low["annual_ticketing_potential_usd"], high["annual_ticketing_potential_usd"])
        self.assertEqual(low["opportunity"], high["opportunity"])
        self.assertLess(ordering(low), ordering(high))
        self.assertEqual(assess(numbers(difficulty=""))["switching_difficulty"], "unknown")

    def test_higher_potential_changes_rank_within_same_action_category(self):
        a = organiser("ACCOUNT_A", **numbers(price="100"))
        z = organiser("ACCOUNT_Z", **numbers(price="10"))
        before, _, _ = self.run_rows([a, z])
        z.update(numbers(price="300"))
        after, _, _ = self.run_rows([a, z])
        self.assertEqual([r["account_id"] for r in before["work"]], ["ACCOUNT_A", "ACCOUNT_Z"])
        self.assertEqual([r["account_id"] for r in after["work"]], ["ACCOUNT_Z", "ACCOUNT_A"])
        self.assertEqual(after["work"][0]["simulated_commercial"]["opportunity"], "High")
        self.assertEqual({r["underlying_queue"] for r in after["work"]}, {"evidence_research"})

    def test_high_value_does_not_override_restrictions_callback_or_cooldown(self):
        due = organiser("DUE", **numbers(price="10"), callback_requested_at="2026-10-19T12:00:00Z",
                        callback_due_at="2026-10-22T12:00:00Z", callback_channel="phone")
        restricted = organiser("RESTRICTED", **numbers(price="5000"), restriction_state="global_do_not_contact",
                               last_interaction_at="2026-10-21T14:00:00Z", last_interaction_outcome="positive_reply", last_interaction_channel="email")
        recent = organiser("RECENT", **numbers(price="2000"), last_interaction_at="2026-10-21T14:00:00Z",
                           last_interaction_outcome="no_answer", last_interaction_channel="phone")
        cold = organiser("COLD", **numbers(price="1000"))
        later_due = organiser("LATER", **numbers(price="5000"), callback_requested_at="2026-10-19T12:00:00Z",
                              callback_due_at="2026-10-22T13:00:00Z", callback_channel="phone")
        plan, _, output = self.run_rows([restricted, recent, cold, due, later_due])
        items = {r["account_id"]: r for r in plan["work"]}
        self.assertEqual(plan["work"][0]["account_id"], "DUE")
        self.assertEqual(plan["work"][1]["account_id"], "LATER")
        self.assertEqual(items["DUE"]["underlying_queue"], "callback_review")
        self.assertEqual(items["RESTRICTED"]["underlying_queue"], "restricted")
        self.assertEqual(items["RECENT"]["underlying_queue"], "deferred")
        self.assertIn("Wait", items["RECENT"]["work_title"])
        proposed = json.loads((output / "handoff_PROPOSED.json").read_text())
        self.assertFalse({"RECENT", "RESTRICTED"} & {r["account_id"] for r in proposed["tasks"]})

    def test_simulated_inputs_do_not_become_public_evidence_or_sales_ready(self):
        _, _, output = self.run_rows([organiser("UNKNOWN", **numbers(price="5000"))])
        public = json.loads((output / "imported_public.json").read_text())["organisers"][0]
        self.assertNotIn("simulated_commercial", public)
        self.assertIsNone(public["public_evidence"])
        core = json.loads((output / "core_queue.json").read_text())["accounts"][0]
        self.assertIsNone(core["commercial_potential"]["annual_ticket_value_usd"])
        self.assertEqual(core["commercial_potential"]["activity_signal"], "paid_activity_unestablished")
        self.assertEqual(core["action"]["queue"], "evidence_research")
        self.assertFalse(core["action"]["outreach_authorized"])
        self.assertTrue((output / "simulated_commercial.json").is_file())

    def test_view_and_csv_show_assumptions_unknowns_and_next_actions(self):
        _, _, output = self.run_rows([organiser("HIGH", **numbers(price="300")), organiser("MISSING", **numbers(tickets=""))])
        view = present(output, "test")
        rows = {r["account_id"]: r for r in view["rows"]}
        self.assertEqual(rows["HIGH"]["simulated_commercial"]["annual_ticketing_potential_usd"], 300000)
        self.assertEqual(rows["MISSING"]["simulated_commercial"]["opportunity"], "Unknown")
        self.assertIn("Verify organiser identity", rows["HIGH"]["next_action"])
        self.assertIn("synthetic", results_csv(view).decode("utf-8-sig"))
        self.assertIn("paid tickets per event", rows["MISSING"]["simulated_commercial"]["assumptions"])

    def test_illustrative_4_percent_plus_one_dollar_example(self):
        result = assess(numbers(events="12", tickets="200", price="40"))
        self.assertEqual(result["annual_paid_tickets"], 2400)
        self.assertEqual(result["annual_ticketing_gmv_usd"], 96000)
        self.assertEqual(result["annual_ticketing_potential_usd"], 96000)
        self.assertEqual(result["illustrative_annual_gross_fee_revenue_usd"], 6240)
        self.assertIn("not profit or verified Humanitix revenue", result["fee_limitations"])
        self.assertIn("Processing costs are not deducted", result["fee_limitations"])

    def test_proportional_fee_assumption_changes_preserve_order_and_actions(self):
        rows = [organiser("A", **numbers(price="40")), organiser("B", **numbers(price="100"))]
        before, _, _ = self.run_rows(rows)
        settings = {**load_settings(), "fee_percentage": 2, "fee_per_paid_ticket_usd": 0.5}
        after, _, output = self.run_rows(rows, settings)
        self.assertEqual([r["account_id"] for r in before["work"]], [r["account_id"] for r in after["work"]])
        for old, new in zip(before["work"], after["work"]):
            self.assertEqual(old["work_title"], new["work_title"])
            self.assertEqual(old["simulated_commercial"]["annual_ticketing_gmv_usd"], new["simulated_commercial"]["annual_ticketing_gmv_usd"])
            self.assertEqual(old["simulated_commercial"]["illustrative_annual_gross_fee_revenue_usd"] / 2,
                             new["simulated_commercial"]["illustrative_annual_gross_fee_revenue_usd"])
        saved = json.loads((output / "simulated_commercial.json").read_text())
        self.assertEqual(saved["metadata"]["settings"]["fee_percentage"], 2)

    def test_fee_ranking_can_reverse_gmv_ranking_without_changing_actions(self):
        # Both accounts have the same action category. More paid tickets can
        # create more fee opportunity even with lower gross ticket sales.
        premium = organiser("PREMIUM", **numbers(events="10", tickets="100", price="100"))
        recurring = organiser("RECURRING", **numbers(events="20", tickets="200", price="20"))
        plan, _, output = self.run_rows([premium, recurring])
        self.assertEqual([row["account_id"] for row in plan["work"]], ["RECURRING", "PREMIUM"])
        first, second = [row["simulated_commercial"] for row in plan["work"]]
        self.assertEqual((first["annual_ticketing_gmv_usd"], first["illustrative_annual_gross_fee_revenue_usd"]), (80000, 7200))
        self.assertEqual((second["annual_ticketing_gmv_usd"], second["illustrative_annual_gross_fee_revenue_usd"]), (100000, 5000))
        view = present(output, "test")
        self.assertEqual(view["rows"][0]["account_id"], "RECURRING")
        exported = list(csv.DictReader(io.StringIO(results_csv(view).decode("utf-8-sig"))))
        self.assertEqual(exported[0]["Account ID"], "RECURRING")
        # Configurable per-ticket fees affect ranking, not qualification.
        percentage_only = {**load_settings(), "fee_per_paid_ticket_usd": 0}
        changed, _, _ = self.run_rows([premium, recurring], percentage_only)
        self.assertEqual([row["account_id"] for row in changed["work"]], ["PREMIUM", "RECURRING"])
        self.assertEqual({row["underlying_queue"] for row in changed["work"]}, {"evidence_research"})

    def test_unknown_fee_never_falls_back_to_gmv_and_explicit_zero_is_known(self):
        known = assess(numbers(price="0"), {**load_settings(), "fee_percentage": 0, "fee_per_paid_ticket_usd": 0})
        unknown = assess(numbers(price="5000"), {**load_settings(), "fee_percentage": None})
        self.assertEqual(known["illustrative_annual_gross_fee_revenue_usd"], 0)
        self.assertIsNone(unknown["illustrative_annual_gross_fee_revenue_usd"])
        self.assertEqual(unknown["annual_ticketing_gmv_usd"], 5000000)
        self.assertLess(ordering(known), ordering(unknown))

    def test_simplified_actions_explain_callback_restriction_and_exact_wait(self):
        common = {"last_interaction_at": "2026-10-21T14:00:00Z", "last_interaction_outcome": "no_answer", "last_interaction_channel": "phone"}
        callback = {"callback_requested_at": "2026-10-19T12:00:00Z", "callback_due_at": "2026-10-22T12:00:00Z", "callback_channel": "phone"}
        _, _, output = self.run_rows([
            organiser("DUE", **callback),
            organiser("RECENT", **common, **numbers(price="5000")),
            organiser("RESTRICTED", restriction_state="global_do_not_contact", **callback),
            organiser("FUTURE", callback_requested_at="2026-10-19T12:00:00Z", callback_due_at="2026-11-01T12:00:00Z", callback_channel="phone"),
            organiser("CLEARANCE", crm_match_state="unknown", **callback),
            organiser("COLD"),
        ])
        rows = {r["account_id"]: r for r in present(output, "test")["rows"]}
        self.assertIn("22 Oct 2026, 12:00 UTC", rows["DUE"]["explanation"])
        self.assertIn("do not contact", rows["DUE"]["next_action"])
        self.assertIn("28 Oct 2026, 14:00 UTC", rows["RECENT"]["next_action"])
        self.assertIn("cooldown/attempt", rows["RECENT"]["explanation"])
        self.assertIn("global do-not-contact", rows["RESTRICTED"]["explanation"])
        self.assertIn("conflict", rows["RESTRICTED"]["next_action"])
        self.assertIn("1 Nov 2026, 12:00 UTC", rows["FUTURE"]["next_action"])
        self.assertEqual(rows["CLEARANCE"]["priority"], "Due CRM check")
        self.assertIn("identity match", rows["CLEARANCE"]["explanation"])
        self.assertIn("Verify organiser identity", rows["COLD"]["next_action"])
        self.assertTrue(all("Within comparable" not in r["explanation"] for r in rows.values()))

    def test_configurable_gmv_thresholds_only_change_bands(self):
        row = numbers(events="12", tickets="200", price="40")
        baseline = assess(row)
        changed = assess(row, {**load_settings(), "gmv_thresholds_usd": {"High": 90000, "Medium": 20000}})
        self.assertEqual(baseline["opportunity"], "Medium")
        self.assertEqual(changed["opportunity"], "High")
        self.assertEqual(baseline["annual_ticketing_gmv_usd"], changed["annual_ticketing_gmv_usd"])
        self.assertEqual(baseline["illustrative_annual_gross_fee_revenue_usd"], changed["illustrative_annual_gross_fee_revenue_usd"])
        self.assertIn("Unvalidated", changed["thresholds_label"])
        self.assertEqual(changed["threshold_basis"], "annual_ticketing_gmv_usd")
        settings_file = self.root / "commercial_settings.json"
        settings_file.write_text(json.dumps({**load_settings(), "gmv_thresholds_usd": {"High": 90000, "Medium": 20000}}))
        with patch("priority_engine.commercial.SETTINGS_PATH", settings_file):
            _, _, output = self.run_rows([organiser("CONFIGURED", **row)])
            view = present(output, "configured")
        self.assertEqual(view["rows"][0]["simulated_commercial"]["opportunity"], "High")
        self.assertEqual(view["rows"][0]["simulated_commercial"]["thresholds_usd"]["High"], 90000)

    def test_missing_fee_assumptions_are_unknown_and_explicit_zero_is_valid(self):
        for field in ("fee_percentage", "fee_per_paid_ticket_usd"):
            settings = {**load_settings(), field: None}
            result = assess(numbers(), settings)
            self.assertEqual(result["annual_ticketing_gmv_usd"], 100000)
            self.assertIsNone(result["illustrative_annual_gross_fee_revenue_usd"])
        result = assess(numbers(), {**load_settings(), "fee_percentage": 0, "fee_per_paid_ticket_usd": 0})
        self.assertEqual(result["illustrative_annual_gross_fee_revenue_usd"], 0)

    def test_display_export_and_handoff_keep_fee_assumptions_labelled(self):
        _, _, output = self.run_rows([organiser("EXAMPLE", **numbers(events="12", tickets="200", price="40"))])
        view = present(output, "test")
        exported = next(csv.DictReader(io.StringIO(results_csv(view).decode("utf-8-sig"))))
        self.assertEqual(exported["Illustrative annual gross fee revenue USD"], "6240")
        self.assertIn("4%", exported["Illustrative fee assumptions"])
        self.assertIn("unvalidated", exported["GMV threshold assumptions"].lower())
        handoff = json.loads((output / "handoff_PROPOSED.json").read_text())
        self.assertIn("Processing costs are not deducted", handoff["notes"][0]["standard_properties"]["Note body"])


if __name__ == "__main__":
    unittest.main()
