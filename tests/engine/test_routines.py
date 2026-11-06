"""``aegis_desk.routines``: the routine manifest model, its structural rules, the weekly budget, the Auto-review
texts and the day audit of the heartbeat history.

The real ``routines/manifest.toml`` and ``routines/autoreview.toml`` of this checkout are pinned by hash and read
through the library; every structural rule is exercised on a mutated copy of the real manifest's text (one defect
per case), so each refusal is attributable to exactly that change. The day audit runs on history records written
by ``heartbeat.write_heartbeat`` into a temporary desk root, at fixed instants (no wall clock). 2026-11-03 is EST
(UTC-5): 07:30 ET is 12:30Z, 10:05 ET 15:05Z, 15:35 ET 20:35Z, 17:10 ET 22:10Z.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import dataclasses
import datetime
import hashlib
import json
import os
import tempfile
import unittest
from typing import Any, Dict, List, Optional

from aegis_desk import alert_lint, desk_paths, heartbeat, market_clock, routines, signals, toml_compat

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MANIFEST = os.path.join(ROOT, "routines", "manifest.toml")
AUTOREVIEW = os.path.join(ROOT, "routines", "autoreview.toml")
MANIFEST_SHA256 = "07708dcb8b09a2ddde620f9096fa14d852e35857063cfe2f0282860cd1d343c0"
AUTOREVIEW_SHA256 = "840001327b3afa304b8d5280d2ae0d37fc67bffae45ee623a297e7672874aa62"
STAGE1_IDS = ["preopen", "book-check", "final-call", "eod", "eod-brief", "post-expiry"]
# PR 3b decision S14: the plan's Stage-2 roll-window routine is dropped (the Structuring Desk never posts alerts).
PLANNED_IDS = STAGE1_IDS + ["opening-marks", "midday-limits", "event-sheet", "t1-call", "pre-event-stress",
                            "weekend-stress", "calendar-build", "journal-close", "weekly-review",
                            "rules-sanity", "rulebook-memo"]
STRUCTURING_TOOLS = ("exit_ticket", "roll_analysis", "propose")
STRUCTURING_REFUSAL = "owner-requested only: a structuring text names candidates and never runs in a routine"
OWNER_AUTHORITY_TEXT = ("Ask first: any command that carries --owner-tapped or --allow-rth, including ack.py snooze, ack.py acted and ack.py config; rules_adopt.py; desk_init.py; exit_ticket.py, roll_analysis.py and propose.py. These record the owner's own decisions or answer the owner's own request: run them only after the owner taps approve on this card.")
UTC = datetime.timezone.utc
TUESDAY = datetime.date(2026, 11, 3)
SATURDAY = datetime.date(2026, 11, 21)
THANKSGIVING = datetime.date(2026, 11, 26)
DAY_AFTER_THANKSGIVING = datetime.date(2026, 11, 27)


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def et(day: datetime.date, hhmm: str, second: int = 0) -> datetime.datetime:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return market_clock.from_et(datetime.datetime(day.year, day.month, day.day, hour, minute, second))


def order_shaped(text: str) -> List[str]:
    return [pattern.pattern for pattern in alert_lint.FORBIDDEN_PATTERNS if pattern.search(text)]


class ConstantsTest(unittest.TestCase):
    def test_step_tools_are_the_eight_chain_tools(self) -> None:
        # O4: no ack and no routine_audit in any chain.
        self.assertEqual(("assignment_watch", "book", "book_check", "csv_recon", "desk_doctor", "eod_summary",
                          "expiry_checklist", "trigger_levels"), routines.STEP_TOOLS)
        self.assertNotIn("ack", routines.STEP_TOOLS)
        self.assertNotIn("routine_audit", routines.STEP_TOOLS)
        for name in STRUCTURING_TOOLS:
            self.assertNotIn(name, routines.STEP_TOOLS)

    def test_the_structuring_tools_are_refused_step_tools(self) -> None:
        # PR 3b decision S9 (designer C's D7.1): owner-requested only, never in a routine.
        self.assertEqual(("ack", "routine_audit") + STRUCTURING_TOOLS, tuple(routines.REFUSED_STEP_TOOLS))
        for name in STRUCTURING_TOOLS:
            self.assertEqual(STRUCTURING_REFUSAL, routines.REFUSED_STEP_TOOLS[name])
        self.assertEqual([], order_shaped(STRUCTURING_REFUSAL))
        self.assertEqual(frozenset({"book_check", "book", "csv_recon", "expiry_checklist", "assignment_watch",
                                    "trigger_levels"}), routines.FIXTURE_SET_TOOLS)
        self.assertTrue(routines.FIXTURE_SET_TOOLS <= set(routines.STEP_TOOLS))

    def test_flag_tables_and_limits(self) -> None:
        self.assertEqual(("--json", "--schema", "--timeout", "--desk-root", "--offline", "--now", "--fixture-set",
                          "--routine", "--scheduled-et", "--next-run-utc", "-h", "--help"), routines.ROUTINE_OWNED_FLAGS)
        self.assertEqual(("--owner-tapped", "--allow-rth", "--diff", "--allow-dirty"), routines.FORBIDDEN_CHAIN_FLAGS)
        self.assertEqual(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), routines.DAYS)
        self.assertEqual(("always", "transitions", "fired"), routines.POSTS)
        self.assertEqual(20, routines.MIN_GAP_MIN)
        self.assertEqual(("08:00", "10:00"), routines.DELAY_WINDOW_ET)
        self.assertEqual(1000, routines.ALLOW_LIMIT_CHARS)
        self.assertEqual(45, routines.MISSED_AFTER_MIN)
        self.assertEqual(20, routines.LATE_AFTER_MIN)
        self.assertEqual("routines/manifest.toml", routines.MANIFEST_REL)
        self.assertEqual("routines/autoreview.toml", routines.AUTOREVIEW_REL)
        self.assertEqual(1, routines.SCHEMA_VERSION)
        self.assertEqual("Run python3 /workspace/aegis-grok-desk/scripts/routine.py %s --slot {slot} and post its "
                         "output verbatim.", routines.PROMPT_TEMPLATE)
        self.assertEqual(("allow", "ask_first_writes", "ask_first_owner_authority"), routines.RULE_KEYS)

    def test_checkout_paths(self) -> None:
        self.assertEqual(MANIFEST, routines.manifest_path())
        self.assertEqual(AUTOREVIEW, routines.autoreview_path())
        self.assertEqual(os.path.join("/x", "routines", "manifest.toml"), routines.manifest_path("/x"))


class RealManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = routines.load_manifest()

    def test_file_is_pinned(self) -> None:
        with open(MANIFEST, "rb") as handle:
            self.assertEqual(MANIFEST_SHA256, hashlib.sha256(handle.read()).hexdigest())
        self.assertEqual(MANIFEST_SHA256, self.manifest.sha256)
        self.assertEqual(MANIFEST, self.manifest.path)

    def test_load_by_file_directory_or_default_is_the_same_manifest(self) -> None:
        self.assertEqual(self.manifest, routines.load_manifest(MANIFEST))
        self.assertEqual(self.manifest, routines.load_manifest(ROOT))

    def test_stage1_routines_in_order(self) -> None:
        self.assertEqual(STAGE1_IDS, list(self.manifest.routine_ids))
        owners = {r.id: r.owner for r in self.manifest.routines}
        self.assertEqual({"preopen": "desk-lead", "book-check": "exit-sentinel", "final-call": "position-control",
                          "eod": "exit-sentinel", "eod-brief": "desk-lead", "post-expiry": "position-control"}, owners)
        self.assertEqual({1}, {r.stage for r in self.manifest.routines})
        self.assertEqual(["lean", "active"], list(self.manifest.profiles))
        self.assertFalse(self.manifest.profiles["lean"].additive)
        self.assertTrue(self.manifest.profiles["active"].additive)
        self.assertEqual("about 8 runs per weekday plus Saturday", self.manifest.profiles["lean"].description)

    def test_chains_follow_o4_and_o5(self) -> None:
        by_id = {r.id: r for r in self.manifest.routines}
        self.assertEqual(("eod_summary --brief",), by_id["eod-brief"].chain)
        self.assertEqual(("desk_doctor --quick", "csv_recon", "expiry_checklist --week", "eod_summary"),
                         by_id["preopen"].chain)
        self.assertEqual(("expiry_checklist --final-call", "expiry_checklist --tomorrow", "assignment_watch"),
                         by_id["final-call"].chain)
        self.assertEqual(("book --snapshot --report", "trigger_levels --from-snapshot", "csv_recon"), by_id["eod"].chain)
        self.assertEqual(("csv_recon",), by_id["post-expiry"].chain)
        self.assertEqual(("15:20",), by_id["final-call"].slots())
        for routine in self.manifest.routines:
            for steps in routine.chains():
                for step in steps:
                    tokens = routines.step_tokens(step)
                    self.assertNotIn("ack", tokens, routine.id)
                    self.assertIn(tokens[0], routines.STEP_TOOLS, step)
        self.assertNotIn("et_early_close", read(MANIFEST))
        self.assertNotIn("12:20", read(MANIFEST))

    def test_book_check_slots_and_slot_chains(self) -> None:
        routine = self.manifest.by_id("book-check")
        self.assertEqual(("10:05", "12:05", "14:05", "15:35"), routine.slots("lean"))
        self.assertEqual(("10:05", "11:05", "12:05", "13:05", "14:05", "14:35", "15:05", "15:35"), routine.slots("active"))
        self.assertEqual(routine.slots("active"), routine.slots())
        self.assertEqual(("book_check --post", "trigger_levels"), routine.chain_for("10:05"))
        self.assertEqual(("book_check --post",), routine.chain_for("11:05"))
        self.assertEqual(("book_check --post --watch-alive",), routine.chain_for("12:05"))
        self.assertEqual(("book_check --post",), routine.chain_for("14:05"))
        self.assertEqual(("book_check --post --summary",), routine.chain_for("15:35"))
        with self.assertRaises(ValueError):
            routine.chain_for("10:06")
        self.assertEqual(routines.Dependency("preopen", 20), routine.depends_on)
        self.assertEqual(("book_check",), routine.post)
        self.assertEqual("transitions", routine.posts)
        self.assertEqual({"book_check", "trigger_levels"}, routine.tools())

    def test_prompts_and_policies(self) -> None:
        for routine in self.manifest.routines:
            self.assertEqual(routines.PROMPT_TEMPLATE % routine.id, routine.prompt)
            self.assertTrue(set(routine.post) <= routine.tools(), routine.id)
            self.assertTrue(routine.quiet_rule and routine.approval_boundary, routine.id)
        self.assertEqual(("Sat",), self.manifest.by_id("post-expiry").days)
        self.assertFalse(self.manifest.by_id("post-expiry").trading_days_only)
        self.assertEqual(routines.Dependency("eod", 20), self.manifest.by_id("eod-brief").depends_on)
        self.assertIsNone(self.manifest.by_id("nope"))

    def test_texts_carry_no_order_shaped_language(self) -> None:
        texts = [profile.description for profile in self.manifest.profiles.values()]
        for routine in self.manifest.routines:
            texts.extend([routine.quiet_rule, routine.approval_boundary, routine.prompt])
        for text in texts:
            self.assertEqual([], order_shaped(text), text)
        self.assertEqual([], order_shaped(read(MANIFEST)))

    def test_budget_is_the_plans_count(self) -> None:
        # O5: lean 41 and active 61 runs a week (no 12:20 early-close routine).
        self.assertEqual(41, routines.budget(self.manifest, "lean"))
        self.assertEqual(61, routines.budget(self.manifest, "active"))
        with self.assertRaises(ValueError):
            routines.budget(self.manifest, "turbo")

    def test_runs_on(self) -> None:
        preopen = self.manifest.by_id("preopen")
        self.assertTrue(preopen.runs_on(TUESDAY))
        self.assertFalse(preopen.runs_on(SATURDAY))
        self.assertFalse(preopen.runs_on(THANKSGIVING))
        self.assertTrue(preopen.runs_on(DAY_AFTER_THANKSGIVING))  # an early-close day is a trading day
        self.assertFalse(preopen.runs_on(TUESDAY, {TUESDAY: "owner-injected closure"}))
        post_expiry = self.manifest.by_id("post-expiry")
        self.assertTrue(post_expiry.runs_on(SATURDAY))
        self.assertFalse(post_expiry.runs_on(TUESDAY))

    def test_day_slots_are_in_time_order(self) -> None:
        slots = routines.day_slots(self.manifest, "lean", TUESDAY)
        self.assertEqual([("preopen", "07:30"), ("book-check", "10:05"), ("book-check", "12:05"),
                          ("book-check", "14:05"), ("final-call", "15:20"), ("book-check", "15:35"),
                          ("eod", "16:35"), ("eod-brief", "17:10")],
                         [(slot.routine_id, slot.scheduled_et) for slot in slots])
        self.assertEqual(datetime.datetime(2026, 11, 3, 12, 30, tzinfo=UTC), slots[0].scheduled_utc)
        self.assertEqual(12, len(routines.day_slots(self.manifest, "active", TUESDAY)))
        self.assertEqual([("post-expiry", "09:30")],
                         [(s.routine_id, s.scheduled_et) for s in routines.day_slots(self.manifest, "lean", SATURDAY)])
        self.assertEqual((), routines.day_slots(self.manifest, "lean", THANKSGIVING))
        # Daylight time: 2026-10-30 is EDT (UTC-4).
        edt = routines.day_slots(self.manifest, "lean", datetime.date(2026, 10, 30))
        self.assertEqual(datetime.datetime(2026, 10, 30, 14, 5, tzinfo=UTC), edt[1].scheduled_utc)


class StructuralRulesTest(unittest.TestCase):
    """One defect per case on a copy of the real manifest's text."""

    def setUp(self) -> None:
        self.text = read(MANIFEST)

    def mutate(self, old: str, new: str, text: Optional[str] = None) -> str:
        base = self.text if text is None else text
        self.assertEqual(1, base.count(old), old)
        return base.replace(old, new)

    def problems(self, text: str) -> List[str]:
        with self.assertRaises(routines.ManifestError) as caught:
            routines.parse_manifest_text(text, "routines/manifest.toml")
        self.assertTrue(caught.exception.problems)
        self.assertIn("routines/manifest.toml", str(caught.exception))
        return caught.exception.problems

    def assert_refused(self, text: str, *needles: str) -> List[str]:
        problems = self.problems(text)
        joined = "\n".join(problems)
        for needle in needles:
            self.assertIn(needle, joined)
        return problems

    def test_real_text_parses(self) -> None:
        manifest = routines.parse_manifest_text(self.text, "routines/manifest.toml")
        self.assertEqual(STAGE1_IDS, list(manifest.routine_ids))

    def test_skeleton_manifest_is_valid(self) -> None:
        manifest = routines.parse_manifest_text("# Gate fixture routine manifest.\nschema_version = 1\n")
        self.assertEqual((), manifest.routines)
        self.assertEqual(0, routines.budget(manifest, "lean"))
        self.assertEqual((), routines.day_slots(manifest, "lean", TUESDAY))

    def test_schema_and_top_level_keys(self) -> None:
        self.assert_refused(self.mutate("schema_version = 1\n", "schema_version = 2\n"), "schema_version must be 1")
        self.assert_refused(self.mutate("schema_version = 1\n", ""), "schema_version must be 1")
        self.assert_refused(self.mutate("schema_version = 1\n", "schema_version = 1\nextra = 3\n"),
                            "unknown top-level key 'extra'")
        self.assert_refused("schema_version = 1\n[profiles.turbo]\ndescription = \"x\"\n",
                            "profile 'turbo' is not a cadence profile (lean, active)")

    def test_toml_syntax_error_is_a_manifest_error(self) -> None:
        self.assert_refused(self.text + "\n[[routine]\n", "not valid TOML")

    def test_duplicate_id(self) -> None:
        self.assert_refused(self.mutate('id = "eod-brief"', 'id = "eod"'), "duplicate routine id 'eod'")

    def test_bad_id(self) -> None:
        self.assert_refused(self.mutate('id = "post-expiry"', 'id = "Post_Expiry"'), "routine id must match")

    def test_unknown_profile(self) -> None:
        text = self.mutate('id = "post-expiry"\nowner = "position-control"\nstage = 1\nprofiles = ["lean", "active"]\n'
                           'et = { lean = ["09:30"], active = ["09:30"] }',
                           'id = "post-expiry"\nowner = "position-control"\nstage = 1\nprofiles = ["lean", "turbo"]\n'
                           'et = { lean = ["09:30"], turbo = ["09:30"] }')
        self.assert_refused(text, "routine post-expiry: profile 'turbo' is not declared under [profiles]")

    def test_bad_time(self) -> None:
        self.assert_refused(self.mutate('et = { lean = ["07:30"], active = ["07:30"] }',
                                        'et = { lean = ["7:30"], active = ["07:30"] }'),
                            "routine preopen: et.lean slot '7:30' is not HH:MM")
        self.assert_refused(self.mutate('et = { lean = ["07:30"], active = ["07:30"] }',
                                        'et = { lean = ["24:00"], active = ["07:30"] }'),
                            "routine preopen: et.lean slot '24:00' is not HH:MM")

    def test_slot_lists_are_sorted_unique_and_per_listed_profile(self) -> None:
        self.assert_refused(self.mutate('et = { lean = ["16:35"], active = ["16:35"] }',
                                        'et = { lean = ["16:35", "16:30"], active = ["16:30", "16:35"] }'),
                            "routine eod: et.lean slots must be sorted and unique")
        self.assert_refused(self.mutate('et = { lean = ["16:35"], active = ["16:35"] }',
                                        'et = { lean = ["16:35", "16:35"], active = ["16:35"] }'),
                            "routine eod: et.lean slots must be sorted and unique")
        self.assert_refused(self.mutate('et = { lean = ["16:35"], active = ["16:35"] }', 'et = { lean = ["16:35"] }'),
                            "routine eod: et must hold one slot list per listed profile")
        self.assert_refused(self.mutate('et = { lean = ["16:35"], active = ["16:35"] }',
                                        'et = { lean = [], active = ["16:35"] }'),
                            "routine eod: et.lean must be a non-empty list of HH:MM slots")

    def test_additive_profile_contains_the_other_profiles_slots(self) -> None:
        self.assert_refused(self.mutate('et = { lean = ["16:35"], active = ["16:35"] }',
                                        'et = { lean = ["16:35"], active = ["16:40"] }'),
                            "routine eod: additive profile active must contain lean's slots (missing 16:35)")
        self.assert_refused(self.mutate('id = "eod"\nowner = "exit-sentinel"\nstage = 1\nprofiles = ["lean", "active"]\n'
                                        'et = { lean = ["16:35"], active = ["16:35"] }',
                                        'id = "eod"\nowner = "exit-sentinel"\nstage = 1\nprofiles = ["lean"]\n'
                                        'et = { lean = ["16:35"] }'),
                            "routine eod: a routine of profile lean must also list the additive profile active")

    def test_bad_day(self) -> None:
        self.assert_refused(self.mutate('days = ["Sat"]', 'days = ["Saturday"]'),
                            "routine post-expiry: day 'Saturday' is not one of Mon, Tue, Wed, Thu, Fri, Sat, Sun")
        self.assert_refused(self.mutate('days = ["Sat"]', 'days = ["Sat", "Sat"]'),
                            "routine post-expiry: days must be unique")
        self.assert_refused(self.mutate('days = ["Sat"]', 'days = []'), "routine post-expiry: days must be a non-empty list")

    def test_delay_window(self) -> None:
        self.assert_refused(self.mutate('et = { lean = ["07:30"], active = ["07:30"] }',
                                        'et = { lean = ["08:30"], active = ["08:30"] }'),
                            "routine preopen: slot 08:30 falls in the delay window 08:00-10:00 ET on a weekday")
        self.assert_refused(self.mutate('et = { lean = ["07:30"], active = ["07:30"] }',
                                        'et = { lean = ["08:00"], active = ["08:00"] }'),
                            "slot 08:00 falls in the delay window")
        # Half-open: 10:00 is outside; a Saturday-only 09:30 slot is exempt (post-expiry).
        manifest = routines.parse_manifest_text(self.mutate('lean = ["10:05", "12:05", "14:05", "15:35"]',
                                                            'lean = ["10:00", "12:05", "14:05", "15:35"]').replace(
            'active = ["10:05", "11:05"', 'active = ["10:00", "10:05", "11:05"').replace('"10:05" = [', '"10:00" = ['))
        self.assertIn("10:00", manifest.by_id("book-check").slots("lean"))
        self.assertEqual(("09:30",), routines.load_manifest().by_id("post-expiry").slots())

    def test_routine_owned_flags_are_refused_in_a_chain(self) -> None:
        for flag in ("--json", "--now", "--fixture-set", "--routine", "--scheduled-et", "--next-run-utc", "--desk-root",
                     "--offline", "--timeout", "--schema", "-h", "--help"):
            self.assert_refused(self.mutate('chain = ["csv_recon"]', 'chain = ["csv_recon %s"]' % flag),
                                "routine post-expiry: step 'csv_recon %s': %s is set by routine.py, never by the "
                                "manifest" % (flag, flag))
        self.assert_refused(self.mutate('chain = ["csv_recon"]', 'chain = ["csv_recon --now=2026-11-03T19:50:00Z"]'),
                            "--now is set by routine.py, never by the manifest")

    def test_forbidden_flags_are_refused_in_a_chain(self) -> None:
        for flag in ("--owner-tapped", "--allow-rth", "--diff", "--allow-dirty"):
            self.assert_refused(self.mutate('chain = ["csv_recon"]', 'chain = ["csv_recon %s"]' % flag),
                                "routine post-expiry: step 'csv_recon %s': %s is never allowed in a routine chain"
                                % (flag, flag))

    def test_ack_is_refused_anywhere_in_a_chain(self) -> None:
        # O4 replaces D2-9's "ack only with list" rule.
        self.assert_refused(self.mutate('chain = ["eod_summary --brief"]', 'chain = ["ack list", "eod_summary --brief"]'),
                            "routine eod-brief: step 'ack list': ack is not a step tool")
        self.assert_refused(self.mutate('chain = ["eod_summary --brief"]', 'chain = ["ack config", "eod_summary --brief"]'),
                            "routine eod-brief: step 'ack config': ack is not a step tool")
        self.assert_refused(self.mutate('chain = ["eod_summary --brief"]', 'chain = ["eod_summary --brief ack"]'),
                            "routine eod-brief: step 'eod_summary --brief ack': ack is not a step tool")
        self.assert_refused(self.mutate('"15:35" = ["book_check --post --summary"]',
                                        '"15:35" = ["book_check --post --summary", "ack list"]'),
                            "routine book-check: step 'ack list': ack is not a step tool")

    def test_routine_audit_is_refused_in_a_chain(self) -> None:
        self.assert_refused(self.mutate('chain = ["eod_summary --brief"]',
                                        'chain = ["routine_audit", "eod_summary --brief"]'),
                            "routine eod-brief: step 'routine_audit': routine_audit is not a step tool")

    def test_the_structuring_tools_are_refused_in_a_chain(self) -> None:
        for name in STRUCTURING_TOOLS:
            for step in (name, "%s --trade AG-20261020-01" % name):
                with self.subTest(step=step):
                    self.assert_refused(self.mutate('chain = ["eod_summary --brief"]',
                                                    'chain = ["%s", "eod_summary --brief"]' % step),
                                        "routine eod-brief: step '%s': %s is not a step tool: %s"
                                        % (step, name, STRUCTURING_REFUSAL))
            self.assert_refused(self.mutate('"15:35" = ["book_check --post --summary"]',
                                            '"15:35" = ["book_check --post --summary", "%s"]' % name),
                                "routine book-check: step '%s': %s is not a step tool: %s" % (name, name,
                                                                                              STRUCTURING_REFUSAL))

    def test_check_manifests_reports_a_structuring_step(self) -> None:
        import check_manifests  # the gate script; routines' refusal reaches it through parse_manifest_text
        from tests.gates import _trees
        from tests.gates.test_check_manifests import ROUTINE_MANIFEST, routine_tree

        old = 'chain = ["book_check --post"]'
        self.assertEqual(1, ROUTINE_MANIFEST.count(old))
        for name in STRUCTURING_TOOLS:
            with self.subTest(tool=name):
                with _trees.fixture_tree() as root:
                    routine_tree(root, ROUTINE_MANIFEST.replace(old, 'chain = ["book_check --post", "%s"]' % name))
                    result = check_manifests.run(root)
                self.assertFalse(result["ok"])
                self.assertIn("step '%s': %s is not a step tool: %s" % (name, name, STRUCTURING_REFUSAL),
                              "\n".join(result["errors"]))

    def test_unknown_tool_and_bad_tokens(self) -> None:
        self.assert_refused(self.mutate('chain = ["csv_recon"]', 'chain = ["positions_check"]'),
                            "routine post-expiry: step 'positions_check': positions_check is not a step tool "
                            "(assignment_watch, book, book_check")
        self.assert_refused(self.mutate('chain = ["csv_recon"]', 'chain = ["csv_recon;id"]'),
                            "routine post-expiry: step 'csv_recon;id': token 'csv_recon;id' must match")
        self.assert_refused(self.mutate('chain = ["csv_recon"]', 'chain = ["csv_recon | sh"]'),
                            "token '|' must match")
        self.assert_refused(self.mutate('chain = ["csv_recon"]', 'chain = [""]'), "routine post-expiry: empty step")
        self.assert_refused(self.mutate('chain = ["csv_recon"]', 'chain = []'),
                            "routine post-expiry: chain must be a non-empty list of steps")

    def test_book_check_steps(self) -> None:
        self.assert_refused(self.mutate('chain = ["book_check --post"]', 'chain = ["book_check"]'),
                            "routine book-check: step 'book_check': the book_check step must carry --post")
        self.assert_refused(self.mutate('chain = ["book_check --post"]', 'chain = ["book_check --post", "book_check --post"]'),
                            "routine book-check: at most one book_check step per chain")

    def test_dependencies(self) -> None:
        self.assert_refused(self.mutate('depends_on = { routine = "preopen", min_gap_min = 20 }',
                                        'depends_on = { routine = "pre-open", min_gap_min = 20 }'),
                            "routine book-check: depends_on.routine 'pre-open' is not a routine of this manifest")
        self.assert_refused(self.mutate('depends_on = { routine = "preopen", min_gap_min = 20 }',
                                        'depends_on = { routine = "book-check", min_gap_min = 20 }'),
                            "routine book-check: depends_on.routine must differ from the routine itself")
        self.assert_refused(self.mutate('depends_on = { routine = "preopen", min_gap_min = 20 }',
                                        'depends_on = { routine = "preopen", min_gap_min = 10 }'),
                            "routine book-check: depends_on.min_gap_min must be an integer of at least 20 (found 10)")
        self.assert_refused(self.mutate('depends_on = { routine = "preopen", min_gap_min = 20 }',
                                        'depends_on = { routine = "preopen", min_gap_min = true }'),
                            "depends_on.min_gap_min must be an integer of at least 20")
        self.assert_refused(self.mutate('depends_on = { routine = "preopen", min_gap_min = 20 }',
                                        'depends_on = { routine = "preopen", min_gap_min = 20, soft = true }'),
                            "routine book-check: unknown depends_on key 'soft'")

    def test_gap_violation(self) -> None:
        text = self.mutate('depends_on = { routine = "eod", min_gap_min = 20 }',
                           'depends_on = { routine = "eod", min_gap_min = 40 }')
        self.assert_refused(text, "routine eod-brief: slot 17:10 is 35 min after eod's latest slot 16:35 "
                                  "(depends_on.min_gap_min 40)")

    def test_post_and_posts(self) -> None:
        self.assert_refused(self.mutate('post = ["csv_recon"]', 'post = ["eod_summary"]'),
                            "routine post-expiry: post tool 'eod_summary' is not a step of its chains")
        self.assert_refused(self.mutate('post = ["csv_recon"]', 'post = []'),
                            "routine post-expiry: post must be a non-empty list of unique tool names")
        self.assert_refused(self.mutate('post = ["csv_recon"]\nposts = "fired"', 'post = ["csv_recon"]\nposts = "sometimes"'),
                            "routine post-expiry: posts 'sometimes' is not one of always, transitions, fired")
        self.assert_refused(self.mutate('post = ["csv_recon"]\nposts = "fired"', 'post = ["csv_recon"]\nposts = "transitions"'),
                            "routine post-expiry: posts 'transitions' requires post = [\"book_check\"]")

    def test_slot_chain_keys_are_slots(self) -> None:
        self.assert_refused(self.mutate('"10:05" = ["book_check --post", "trigger_levels"]',
                                        '"10:06" = ["book_check --post", "trigger_levels"]'),
                            "routine book-check: slot_chain key '10:06' is not a slot of the routine")

    def test_prompt_mismatch(self) -> None:
        self.assert_refused(self.mutate("routine.py eod --slot {slot}", "routine.py eod --slot {slot} --allow-dirty"),
                            "routine eod: prompt must be exactly")

    def test_unknown_and_missing_routine_keys(self) -> None:
        self.assert_refused(self.mutate('id = "post-expiry"', 'id = "post-expiry"\ncommand = "csv_recon"'),
                            "routine post-expiry: unknown key 'command'")
        # O5: there is no early-close slot key.
        self.assert_refused(self.mutate('et = { lean = ["15:20"], active = ["15:20"] }',
                                        'et = { lean = ["15:20"], active = ["15:20"] }\net_early_close = "12:20"'),
                            "routine final-call: unknown key 'et_early_close'")
        self.assert_refused(self.mutate('owner = "position-control"\nstage = 1\nprofiles = ["lean", "active"]\n'
                                        'et = { lean = ["09:30"]',
                                        'stage = 1\nprofiles = ["lean", "active"]\net = { lean = ["09:30"]'),
                            "routine post-expiry: missing key 'owner'")

    def test_field_types(self) -> None:
        self.assert_refused(self.mutate('id = "eod"\nowner = "exit-sentinel"\nstage = 1',
                                        'id = "eod"\nowner = "exit-sentinel"\nstage = 0'),
                            "routine eod: stage must be a positive integer")
        self.assert_refused(self.mutate('days = ["Sat"]\ntrading_days_only = false', 'days = ["Sat"]\ntrading_days_only = "no"'),
                            "routine post-expiry: trading_days_only must be true or false")
        self.assert_refused(self.mutate('owner = "position-control"\nstage = 1\nprofiles = ["lean", "active"]\n'
                                        'et = { lean = ["09:30"]',
                                        'owner = "Position Control"\nstage = 1\nprofiles = ["lean", "active"]\n'
                                        'et = { lean = ["09:30"]'),
                            "routine post-expiry: owner 'Position Control' must be an agent file stem")
        quiet = 'quiet_rule = "Always one message: the end-of-day threshold-level sheet from the snapshot."'
        self.assert_refused(self.mutate(quiet, 'quiet_rule = ""'), "routine eod: quiet_rule must be a non-empty string")

    def test_every_problem_is_reported_at_once(self) -> None:
        text = self.mutate('id = "eod-brief"', 'id = "eod"')
        text = self.mutate('days = ["Sat"]', 'days = ["Caturday"]', text)
        problems = self.assert_refused(text, "duplicate routine id 'eod'", "day 'Caturday'")
        self.assertGreaterEqual(len(problems), 2)

    def test_missing_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aegis-routines-") as tmp:
            with self.assertRaises(routines.ManifestError) as caught:
                routines.load_manifest(os.path.join(tmp, "manifest.toml"))
        self.assertIn("missing", str(caught.exception))


class AutoreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = routines.load_manifest()
        self.autoreview = routines.load_autoreview()

    def test_file_is_pinned_and_rendered(self) -> None:
        with open(AUTOREVIEW, "rb") as handle:
            self.assertEqual(AUTOREVIEW_SHA256, hashlib.sha256(handle.read()).hexdigest())
        self.assertEqual(AUTOREVIEW_SHA256, self.autoreview.sha256)
        self.assertEqual(1000, self.autoreview.limit_chars)
        texts = routines.rules_text(self.manifest, self.autoreview)
        self.assertEqual(list(routines.RULE_KEYS), list(texts))
        self.assertEqual({"allow": 506, "ask_first_writes": 315, "ask_first_owner_authority": 333},
                         {key: len(value) for key, value in texts.items()})
        self.assertIn(": preopen, book-check, final-call, eod, eod-brief, post-expiry. These runs", texts["allow"])
        self.assertTrue(texts["allow"].startswith("Allow automatically: the command python3 "
                                                  "/workspace/aegis-grok-desk/scripts/routine.py followed by"))
        self.assertTrue(texts["ask_first_writes"].startswith("Ask first: placing, modifying or cancelling an order;"))
        self.assertIn("rules_adopt.py; desk_init.py; exit_ticket.py, roll_analysis.py and propose.py.",
                      texts["ask_first_owner_authority"])
        # PR 3b decision S9 (designer C's D7.3): exactly this text; one tap per structuring run.
        self.assertEqual(OWNER_AUTHORITY_TEXT, texts["ask_first_owner_authority"])
        for value in texts.values():
            self.assertEqual([], order_shaped(value), value)
            self.assertNotIn("{routine_ids}", value)
        self.assertEqual([], routines.rules_text_problems(self.manifest, self.autoreview))

    def test_allow_text_fits_all_planned_routines(self) -> None:
        planned = dataclasses.replace(self.manifest, routines=tuple(
            dataclasses.replace(self.manifest.routines[0], id=routine_id) for routine_id in PLANNED_IDS))
        texts = routines.rules_text(planned, self.autoreview)
        self.assertEqual(667, len(texts["allow"]), "17 planned routine ids (roll-window dropped)")
        self.assertEqual([], routines.rules_text_problems(planned, self.autoreview))

    def test_too_long_and_unnamed_texts_are_problems(self) -> None:
        long_ids = ["routine-%02d-%s" % (n, "x" * 30) for n in range(14)]
        many = dataclasses.replace(self.manifest, routines=tuple(
            dataclasses.replace(self.manifest.routines[0], id=routine_id) for routine_id in long_ids))
        problems = routines.rules_text_problems(many, self.autoreview)
        self.assertEqual(1, len(problems), problems)
        self.assertIn("[allow_routines] renders to 1046 characters (limit 1000)", problems[0])
        dropped = dataclasses.replace(self.autoreview, allow_template=self.autoreview.allow_template.replace(
            "{routine_ids}", "the desk's routines"))
        problems = routines.rules_text_problems(self.manifest, dropped)
        self.assertIn("[allow_routines] does not name routine preopen", "\n".join(problems))

    def autoreview_problems(self, text: str) -> List[str]:
        with self.assertRaises(routines.ManifestError) as caught:
            routines.parse_autoreview_text(text, "routines/autoreview.toml")
        return caught.exception.problems

    def test_structural_rules(self) -> None:
        text = read(AUTOREVIEW)
        cases = {
            text.replace("limit_chars = 1000", "limit_chars = 999"): "limit_chars must be 1000",
            text.replace("schema_version = 1", "schema_version = 2"): "schema_version must be 1",
            text.replace("{routine_ids}", "every routine"): "[allow_routines].text must contain {routine_ids}",
            text + '\n[extra]\ntext = "x"\n': "unknown top-level key 'extra'",
            text.replace('[ask_first_writes]\ntext = "Ask first: placing',
                         '[ask_first_writes]\nnote = "x"\ntext = "Ask first: placing'): "unknown key 'note' in [ask_first_writes]",
            text.split("[ask_first_owner_authority]")[0]: "[ask_first_owner_authority].text must be a non-empty string",
            "schema_version = 1\n[[x]\n": "not valid TOML",
        }
        for mutated, needle in cases.items():
            self.assertIn(needle, "\n".join(self.autoreview_problems(mutated)), needle)

    def test_missing_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aegis-routines-") as tmp:
            with self.assertRaises(routines.ManifestError) as caught:
                routines.load_autoreview(os.path.join(tmp, "autoreview.toml"))
        self.assertIn("missing", str(caught.exception))


class AuditDayTest(unittest.TestCase):
    """History records written by heartbeat.write_heartbeat, read back as the audit reads them."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-routines-audit-")
        self.paths = desk_paths.for_root(os.path.realpath(self._tmp.name))
        desk_paths.ensure_runtime_dirs(self.paths)
        self.manifest = routines.load_manifest()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def beat(self, routine_id: str, slot: Optional[str], started: datetime.datetime, exit_code: int = 0,
             status: str = "ok") -> None:
        heartbeat.write_heartbeat(self.paths, routine_id, now=started, exit_code=exit_code, status=status,
                                  started_utc=started, finished_utc=started, message_sha256=None,
                                  scheduled_et=slot, tool_version="0.1.0")

    def history(self) -> List[Dict[str, Any]]:
        records, _status, _bad = signals.read_journal(heartbeat.history_path(self.paths))
        return records

    def tuesday(self) -> None:
        self.beat("preopen", "07:30", et(TUESDAY, "07:42"))
        self.beat("book-check", "10:05", et(TUESDAY, "10:36"), exit_code=10, status="fired")
        self.beat("book-check", "12:05", et(TUESDAY, "12:15"))
        self.beat("book-check", "14:05", et(TUESDAY, "14:25"))
        self.beat("book-check", "14:05", et(TUESDAY, "14:31"))  # a second record of one slot counts once
        self.beat("final-call", "15:20", et(TUESDAY, "15:30"), status="quiet")
        self.beat("eod", "16:35", et(TUESDAY, "16:50"))
        self.beat("book-check", None, et(TUESDAY, "13:00"))  # a hand-run book_check: no slot, never counted
        self.beat("eod", "16:35", et(datetime.date(2026, 11, 2), "16:40"))  # another ET date
        self.beat("book-check", "11:05", et(TUESDAY, "11:20"))  # an active-only slot

    @staticmethod
    def names(items: Any) -> List[str]:
        return ["%s %s" % (item.routine_id, item.scheduled_et) for item in items]

    def test_eod_brief_audit_excludes_itself(self) -> None:
        # O15: at 17:10 the brief counts the cadence profile's slots at or before its slot, other routines only.
        self.tuesday()
        audit = routines.audit_day(self.manifest, "lean", self.history(), TUESDAY, et(TUESDAY, "17:10"), (),
                                   exclude=("eod-brief",))
        self.assertEqual(["preopen 07:30", "book-check 10:05", "book-check 12:05", "book-check 14:05",
                          "final-call 15:20", "book-check 15:35", "eod 16:35"], self.names(audit.expected))
        self.assertEqual(["preopen 07:30", "book-check 10:05", "book-check 12:05", "book-check 14:05",
                          "final-call 15:20", "eod 16:35"], self.names(audit.ran))
        self.assertEqual(["book-check 10:05"], self.names(audit.late))
        self.assertEqual(31, audit.late[0].lateness_min)
        self.assertEqual(["book-check 15:35"], self.names(audit.missed))
        self.assertEqual((), audit.pending)
        by_slot = {"%s %s" % (s.routine_id, s.scheduled_et): s for s in audit.expected}
        self.assertEqual(2, by_slot["book-check 14:05"].records)
        self.assertEqual(20, by_slot["book-check 14:05"].lateness_min)  # the earliest record of the slot
        self.assertEqual(10, by_slot["book-check 10:05"].exit_code)
        self.assertEqual("fired", by_slot["book-check 10:05"].status)
        self.assertEqual("quiet", by_slot["final-call 15:20"].status)
        self.assertEqual(0, by_slot["book-check 15:35"].records)
        self.assertIsNone(by_slot["book-check 15:35"].lateness_min)

    def test_through_et_cuts_at_the_runs_slot(self) -> None:
        self.tuesday()
        late_run = routines.audit_day(self.manifest, "lean", self.history(), TUESDAY, et(TUESDAY, "17:40"), (),
                                      exclude=("eod-brief",), through_et="17:10")
        on_time = routines.audit_day(self.manifest, "lean", self.history(), TUESDAY, et(TUESDAY, "17:10"), (),
                                     exclude=("eod-brief",))
        self.assertEqual(on_time, late_run)

    def test_without_exclusion_the_own_slot_is_pending(self) -> None:
        self.tuesday()
        audit = routines.audit_day(self.manifest, "lean", self.history(), TUESDAY, et(TUESDAY, "17:10"), ())
        self.assertEqual(8, len(audit.expected))
        self.assertEqual(["eod-brief 17:10"], self.names(audit.pending))
        self.assertNotIn("eod-brief 17:10", self.names(audit.missed))

    def test_active_profile_counts_its_own_slots(self) -> None:
        self.tuesday()
        audit = routines.audit_day(self.manifest, "active", self.history(), TUESDAY, et(TUESDAY, "17:10"), (),
                                   exclude=("eod-brief",))
        self.assertEqual(11, len(audit.expected))
        self.assertIn("book-check 11:05", self.names(audit.ran))
        self.assertEqual(["book-check 13:05", "book-check 14:35", "book-check 15:05", "book-check 15:35"],
                         self.names(audit.missed))

    def test_missed_needs_45_minutes(self) -> None:
        before = routines.audit_day(self.manifest, "lean", [], TUESDAY, et(TUESDAY, "08:14", 59), ())
        self.assertEqual(["preopen 07:30"], self.names(before.expected))
        self.assertEqual(["preopen 07:30"], self.names(before.pending))
        self.assertEqual((), before.missed)
        at = routines.audit_day(self.manifest, "lean", [], TUESDAY, et(TUESDAY, "08:15"), ())
        self.assertEqual(["preopen 07:30"], self.names(at.missed))
        self.assertEqual((), at.pending)

    def test_late_means_more_than_20_minutes(self) -> None:
        self.beat("preopen", "07:30", et(TUESDAY, "07:50"))
        self.beat("book-check", "10:05", et(TUESDAY, "10:26"))
        audit = routines.audit_day(self.manifest, "lean", self.history(), TUESDAY, et(TUESDAY, "11:00"), ())
        self.assertEqual(["book-check 10:05"], self.names(audit.late))
        self.assertEqual([20, 21], [s.lateness_min for s in audit.ran])

    def test_a_run_before_its_slot_counts_and_shows_its_offset(self) -> None:
        self.beat("book-check", "10:05", et(TUESDAY, "10:00"))
        audit = routines.audit_day(self.manifest, "lean", self.history(), TUESDAY, et(TUESDAY, "10:02"), ())
        self.assertEqual(["preopen 07:30", "book-check 10:05"], self.names(audit.expected))
        self.assertEqual(["book-check 10:05"], self.names(audit.ran))
        self.assertEqual(0, audit.ran[0].lateness_min)
        self.assertEqual(-5, audit.ran[0].offset_min)
        self.assertEqual(["preopen 07:30"], self.names(audit.missed))

    def test_closed_days_expect_only_routines_that_run(self) -> None:
        holiday = routines.audit_day(self.manifest, "lean", [], THANKSGIVING, et(THANKSGIVING, "18:00"), ())
        self.assertEqual((), holiday.expected)
        injected = routines.audit_day(self.manifest, "lean", [], TUESDAY, et(TUESDAY, "18:00"),
                                      {TUESDAY: "owner-injected closure"})
        self.assertEqual((), injected.expected)
        self.beat("post-expiry", "09:30", et(SATURDAY, "09:41"))
        saturday = routines.audit_day(self.manifest, "lean", self.history(), SATURDAY, et(SATURDAY, "12:00"), ())
        self.assertEqual(["post-expiry 09:30"], self.names(saturday.ran))
        self.assertEqual((), saturday.missed)

    def test_early_close_day_expects_the_regular_slots_only(self) -> None:
        # O5: no 12:20 slot; final-call 15:20 runs no step and records a quiet heartbeat.
        self.beat("final-call", "15:20", et(DAY_AFTER_THANKSGIVING, "15:26"), status="quiet")
        audit = routines.audit_day(self.manifest, "lean", self.history(), DAY_AFTER_THANKSGIVING,
                                   et(DAY_AFTER_THANKSGIVING, "16:00"), ())
        self.assertNotIn("12:20", [s.scheduled_et for s in audit.expected])
        self.assertIn("final-call 15:20", self.names(audit.ran))

    def test_malformed_records_are_ignored(self) -> None:
        self.beat("preopen", "07:30", et(TUESDAY, "07:35"))
        records = self.history() + [None, 7, "x", {}, {"routine_id": "preopen", "scheduled_et": "07:30"},
                                    {"routine_id": "preopen", "scheduled_et": "7:30", "run_utc": "2026-11-03T12:31:00Z"},
                                    {"routine_id": "preopen", "scheduled_et": "07:30", "run_utc": "yesterday"}]
        audit = routines.audit_day(self.manifest, "lean", records, TUESDAY, et(TUESDAY, "09:00"), ())
        self.assertEqual(["preopen 07:30"], self.names(audit.ran))
        self.assertEqual(1, audit.ran[0].records)
        self.assertEqual(5, audit.ran[0].lateness_min)

    def test_as_dict_is_json(self) -> None:
        self.tuesday()
        audit = routines.audit_day(self.manifest, "lean", self.history(), TUESDAY, et(TUESDAY, "17:10"), (),
                                   exclude=("eod-brief",))
        document = json.loads(json.dumps(audit.as_dict()))
        self.assertEqual(["expected", "late", "missed", "pending", "ran"], sorted(document))
        self.assertEqual({"routine_id": "book-check", "scheduled_et": "15:35", "scheduled_utc": "2026-11-03T20:35:00+00:00",
                          "records": 0, "started_utc": None, "lateness_min": None, "offset_min": None,
                          "exit_code": None, "status": None}, document["missed"][0])
        self.assertEqual(31, document["late"][0]["lateness_min"])

    def test_timezone_offset_suspected(self) -> None:
        for slot in ("07:30", "10:05", "12:05"):
            hour, minute = slot.split(":")
            self.beat("preopen" if slot == "07:30" else "book-check", slot,
                      et(TUESDAY, "%02d:%s" % (int(hour) + 1, minute)) + datetime.timedelta(minutes=2))
        audit = routines.audit_day(self.manifest, "lean", self.history(), TUESDAY, et(TUESDAY, "13:30"), ())
        self.assertEqual(60, routines.timezone_offset_suspected(audit))
        self._tmp.cleanup()
        self.setUp()
        self.tuesday()
        audit = routines.audit_day(self.manifest, "lean", self.history(), TUESDAY, et(TUESDAY, "17:10"), ())
        self.assertIsNone(routines.timezone_offset_suspected(audit))

    def test_inputs_are_checked(self) -> None:
        with self.assertRaises(ValueError):
            routines.audit_day(self.manifest, "turbo", [], TUESDAY, et(TUESDAY, "17:10"), ())
        with self.assertRaises(ValueError):
            routines.audit_day(self.manifest, "lean", [], TUESDAY, datetime.datetime(2026, 11, 3, 22, 10), ())
        with self.assertRaises(ValueError):
            routines.audit_day(self.manifest, "lean", [], TUESDAY, et(TUESDAY, "17:10"), (), through_et="5pm")


class TomlAgreementTest(unittest.TestCase):
    def test_vendored_reader_agrees_with_tomllib(self) -> None:
        for path in (MANIFEST, AUTOREVIEW):
            document = toml_compat.cross_check(read(path))
            self.assertEqual(1, document["schema_version"])


if __name__ == "__main__":
    unittest.main()
