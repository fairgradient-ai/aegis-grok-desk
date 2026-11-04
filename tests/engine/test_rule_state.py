"""``aegis_desk.rule_state``: the per-(group, rule) state machine behind the exit rules (the brief's "Rule state",
the addendum's decision 17(i)): armed -> pending -> fired -> cooldown -> armed, the confirmation stamps (gap and
window), the single-observation policy, the three re-arm variants bounded to one trading day, the first
observation, the rule-sha reset, ``apply``, the carry-forward on adoption, the post bookkeeping, and the state file
read and written through ``aegis_desk.state`` (atomic, locked). Pure functions over plain JSON-ready dicts; the
module never imports ``exit_rules``."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import ast
import datetime
import json
import os
import tempfile
import unittest
from decimal import Decimal
from typing import Any, Dict, Optional

from aegis_desk import desk_paths, market_clock, rule_state

UTC = datetime.timezone.utc  # noqa: UP017 - datetime.UTC needs 3.11; the floor is 3.9
SHA = "a" * 64
OTHER_SHA = "b" * 64
TODAY = datetime.date(2026, 11, 3)  # Tuesday; EST since 2026-11-01
OPENED = datetime.date(2026, 10, 20)


def at(hour: int, minute: int, day: int = 3, month: int = 11) -> datetime.datetime:
    return datetime.datetime(2026, month, day, hour, minute, tzinfo=UTC)


def policy(input_class: str = "M", confirmations: int = 2, gap: int = 10, window: int = 90,
           rearm: str = "next_trading_day", hysteresis: str = "10", op: str = ">=",
           threshold: Optional[str] = "80") -> Dict[str, Any]:
    return {"input_class": input_class, "confirmations": confirmations, "confirmation_gap_min": gap,
            "confirmation_window_min": window, "rearm": rearm, "hysteresis_pct": Decimal(hysteresis), "op": op,
            "threshold": None if threshold is None else Decimal(threshold)}


def run(entry: Optional[Dict[str, Any]], truth: str, now: datetime.datetime, *, as_of: Optional[datetime.datetime] = None,
        value: Any = Decimal("95.32"), pol: Optional[Dict[str, Any]] = None, single: bool = False,
        opened: Optional[datetime.date] = OPENED, sha: str = SHA, today: datetime.date = TODAY,
        extra: Any = ()) -> rule_state.Step:
    return rule_state.step(entry, truth=truth, as_of=as_of if as_of is not None else now, value=value,
                           policy=pol if pol is not None else policy(), now=now, today=today,
                           single_observation=single, opened=opened, rule_sha=sha, extra_holidays=extra)


class KeysAndEntriesTest(unittest.TestCase):
    def test_state_key_round_trip(self) -> None:
        self.assertEqual("AG-20261020-01|TP80", rule_state.state_key("AG-20261020-01", "TP80"))
        self.assertEqual(("AG-20261020-01", "TP80"), rule_state.split_key("AG-20261020-01|TP80"))
        self.assertEqual(("portfolio", "CONC"), rule_state.split_key(rule_state.state_key("portfolio", "CONC")))
        with self.assertRaises(ValueError):
            rule_state.state_key("A|B", "TP80")
        with self.assertRaises(ValueError):
            rule_state.split_key("no-separator")

    def test_new_entry_has_exactly_the_brief_keys(self) -> None:
        entry = rule_state.new_entry(SHA, OPENED)
        self.assertEqual(rule_state.ENTRY_KEYS, tuple(entry))
        self.assertEqual(("status", "rule_sha", "observations", "fired_at", "fired_value", "last_posted_at", "posts",
                          "rearm_at", "first_observation_at", "opened"), rule_state.ENTRY_KEYS)
        self.assertEqual((rule_state.STATUS_ARMED, SHA, [], 0, "2026-10-20"),
                         (entry["status"], entry["rule_sha"], entry["observations"], entry["posts"], entry["opened"]))
        self.assertIsNone(rule_state.entry_problem(entry))
        json.dumps(entry)  # JSON-ready as it stands: strings, numbers, booleans, null

    def test_entry_problem_names_what_is_wrong(self) -> None:
        entry = rule_state.new_entry(SHA, None)
        self.assertIsNone(rule_state.entry_problem(entry))
        bad = dict(entry, status="sleeping")
        self.assertIn("status", rule_state.entry_problem(bad) or "")
        self.assertIn("posts", rule_state.entry_problem(dict(entry, posts=True)) or "")
        self.assertIn("keys", rule_state.entry_problem(dict(entry, extra=1)) or "")
        self.assertIn("rearm_at", rule_state.entry_problem(dict(entry, status=rule_state.STATUS_COOLDOWN)) or "")
        self.assertIn("fired_at", rule_state.entry_problem(dict(entry, fired_at="2026-11-03T19:50:00")) or "",
                      "a naive instant has no zone")


class EClassTest(unittest.TestCase):
    """Rules of input class E never wait for confirmation."""

    def test_first_true_fires_at_once_with_the_first_observation_flag(self) -> None:
        result = run(None, rule_state.TRUTH_TRUE, at(19, 50), value=13, pol=policy("E", threshold="21", op="<="))
        self.assertEqual((None, rule_state.STATUS_FIRED, True, True),
                         (result.before, result.after, result.fired, result.first_observation))
        self.assertEqual((rule_state.EVENT_SEEN, rule_state.EVENT_FIRED), result.events)
        entry = result.entry
        assert entry is not None
        self.assertEqual(("2026-11-03T19:50:00+00:00", 13, "2026-11-03T19:50:00+00:00"),
                         (entry["fired_at"], entry["fired_value"], entry["first_observation_at"]))

    def test_a_group_opened_today_is_not_a_first_observation(self) -> None:
        result = run(None, rule_state.TRUTH_TRUE, at(19, 50), pol=policy("E"), opened=TODAY)
        self.assertEqual((True, False), (result.fired, result.first_observation))

    def test_an_unknown_opened_date_counts_as_before_today(self) -> None:
        result = run(None, rule_state.TRUTH_TRUE, at(19, 50), pol=policy("E"), opened=None)
        self.assertTrue(result.first_observation, "never silently suppressed: no opened date is not 'opened today'")

    def test_first_false_records_an_armed_entry_and_unknown_records_nothing(self) -> None:
        seen = run(None, rule_state.TRUTH_FALSE, at(19, 50), pol=policy("E"))
        self.assertEqual((None, rule_state.STATUS_ARMED, (rule_state.EVENT_SEEN,)), (seen.before, seen.after, seen.events))
        unseen = run(None, rule_state.TRUTH_UNKNOWN, at(19, 50), pol=policy("E"))
        self.assertEqual((None, None, None, ()), (unseen.entry, unseen.before, unseen.after, unseen.events))

    def test_a_later_true_after_a_false_first_sight_is_not_a_first_observation(self) -> None:
        seen = run(None, rule_state.TRUTH_FALSE, at(19, 50), pol=policy("E"))
        later = run(seen.entry, rule_state.TRUTH_TRUE, at(20, 5), pol=policy("E"))
        self.assertEqual((rule_state.STATUS_FIRED, True, False), (later.after, later.fired, later.first_observation))


class ConfirmationTest(unittest.TestCase):
    """M and G rules need ``confirmations`` TRUE observations, consecutive as-of stamps at least the gap apart and
    all within the window of the newest, unless a single observation suffices."""

    def test_two_confirmations_ten_minutes_apart(self) -> None:
        first = run(None, rule_state.TRUTH_TRUE, at(19, 50), as_of=at(19, 31))
        self.assertEqual((rule_state.STATUS_PENDING, False), (first.after, first.fired))
        self.assertEqual((rule_state.EVENT_SEEN, rule_state.EVENT_PENDING), first.events)
        same_quote = run(first.entry, rule_state.TRUTH_TRUE, at(19, 51), as_of=at(19, 31))
        self.assertEqual((rule_state.STATUS_PENDING, (), 1),
                         (same_quote.after, same_quote.events, len(same_quote.entry["observations"])),
                         "a re-run on the same quote is not a second observation")
        too_close = run(same_quote.entry, rule_state.TRUTH_TRUE, at(19, 58), as_of=at(19, 40))
        self.assertEqual((rule_state.STATUS_PENDING, 1), (too_close.after, len(too_close.entry["observations"])),
                         "9 minutes after the first stamp: the earlier observation stands")
        confirmed = run(too_close.entry, rule_state.TRUTH_TRUE, at(20, 0), as_of=at(19, 41))
        self.assertEqual((rule_state.STATUS_PENDING, rule_state.STATUS_FIRED, True, (rule_state.EVENT_FIRED,)),
                         (confirmed.before, confirmed.after, confirmed.fired, confirmed.events))
        self.assertEqual(["2026-11-03T19:31:00+00:00", "2026-11-03T19:41:00+00:00"],
                         [item["as_of"] for item in confirmed.entry["observations"]])
        self.assertTrue(confirmed.first_observation, "TRUE since the desk first saw it on a position opened earlier")

    def test_observations_outside_the_window_are_dropped(self) -> None:
        first = run(None, rule_state.TRUTH_TRUE, at(18, 0), as_of=at(18, 0))
        late = run(first.entry, rule_state.TRUTH_TRUE, at(19, 35), as_of=at(19, 35))
        self.assertEqual((rule_state.STATUS_PENDING, ["2026-11-03T19:35:00+00:00"]),
                         (late.after, [item["as_of"] for item in late.entry["observations"]]),
                         "95 minutes apart with a 90-minute window: the old stamp no longer confirms")
        fired = run(late.entry, rule_state.TRUTH_TRUE, at(19, 45), as_of=at(19, 45))
        self.assertEqual(rule_state.STATUS_FIRED, fired.after)

    def test_three_confirmations(self) -> None:
        pol = policy(confirmations=3, gap=5, window=30)
        entry = None
        for minute, expected in ((0, rule_state.STATUS_PENDING), (5, rule_state.STATUS_PENDING),
                                 (10, rule_state.STATUS_FIRED)):
            result = run(entry, rule_state.TRUTH_TRUE, at(15, minute), pol=pol)
            self.assertEqual(expected, result.after, minute)
            entry = result.entry
        self.assertEqual(3, len(entry["observations"]))

    def test_one_confirmation_fires_at_once(self) -> None:
        result = run(None, rule_state.TRUTH_TRUE, at(19, 50), pol=policy(confirmations=1))
        self.assertEqual(rule_state.STATUS_FIRED, result.after)

    def test_single_observation_policy(self) -> None:
        result = run(None, rule_state.TRUTH_TRUE, at(19, 50), single=True)
        self.assertEqual((rule_state.STATUS_FIRED, True), (result.after, result.fired))

    def test_false_clears_a_pending_streak(self) -> None:
        first = run(None, rule_state.TRUTH_TRUE, at(19, 30))
        cleared = run(first.entry, rule_state.TRUTH_FALSE, at(19, 45))
        self.assertEqual((rule_state.STATUS_PENDING, rule_state.STATUS_ARMED, (rule_state.EVENT_CLEARED,)),
                         (cleared.before, cleared.after, cleared.events))
        self.assertEqual(([], None), (cleared.entry["observations"], cleared.entry["first_observation_at"]))
        again = run(cleared.entry, rule_state.TRUTH_TRUE, at(19, 55))
        self.assertEqual(rule_state.STATUS_PENDING, again.after)
        fired = run(again.entry, rule_state.TRUTH_TRUE, at(20, 10))
        self.assertEqual((True, False), (fired.fired, fired.first_observation),
                         "the streak broke after the first sight: a later fire is an ordinary transition")

    def test_unknown_leaves_a_pending_streak_alone(self) -> None:
        first = run(None, rule_state.TRUTH_TRUE, at(19, 30))
        unknown = run(first.entry, rule_state.TRUTH_UNKNOWN, at(19, 40), value=None)
        self.assertEqual((rule_state.STATUS_PENDING, (), first.entry), (unknown.after, unknown.events, unknown.entry))

    def test_an_older_quote_is_not_an_observation(self) -> None:
        first = run(None, rule_state.TRUTH_TRUE, at(19, 50), as_of=at(19, 31))
        older = run(first.entry, rule_state.TRUTH_TRUE, at(20, 5), as_of=at(19, 1))
        self.assertEqual((rule_state.STATUS_PENDING, 1), (older.after, len(older.entry["observations"])))


class SingleObservationPolicyTest(unittest.TestCase):
    """Decision 17(i): ``next_run_utc`` None means another observation is coming; one observation suffices only
    when the next run is later than the session's close or the session is not live."""

    def test_policy(self) -> None:
        live = market_clock.session_at(at(19, 50))
        self.assertTrue(live.is_live)
        self.assertFalse(rule_state.single_observation(live, None))
        self.assertFalse(rule_state.single_observation(live, at(20, 50)))
        self.assertFalse(rule_state.single_observation(live, at(21, 0)), "a run at the close itself still observes")
        self.assertTrue(rule_state.single_observation(live, at(21, 35)))
        closed = market_clock.session_at(at(21, 30))
        self.assertTrue(rule_state.single_observation(closed, None))
        pre = market_clock.session_at(at(13, 0))
        self.assertEqual("pre", pre.session)
        self.assertTrue(rule_state.single_observation(pre, None))
        weekend = market_clock.session_at(at(15, 0, day=7))
        self.assertTrue(rule_state.single_observation(weekend, None))
        early = market_clock.session_at(datetime.datetime(2026, 11, 27, 16, 0, tzinfo=UTC))
        self.assertEqual("early_close", early.session)
        self.assertTrue(rule_state.single_observation(early, datetime.datetime(2026, 11, 27, 18, 35, tzinfo=UTC)),
                        "13:00 ET close on the day after Thanksgiving is 18:00 UTC")


class RearmTest(unittest.TestCase):
    def fired(self, pol: Dict[str, Any], now: datetime.datetime = at(19, 50), today: datetime.date = TODAY,
              extra: Any = ()) -> Dict[str, Any]:
        result = run(None, rule_state.TRUTH_TRUE, now, pol=pol, single=True, today=today, extra=extra)
        self.assertEqual(rule_state.STATUS_FIRED, result.after)
        assert result.entry is not None
        return result.entry

    def test_true_while_fired_is_no_transition(self) -> None:
        entry = self.fired(policy())
        again = run(entry, rule_state.TRUTH_TRUE, at(19, 51), single=True)
        self.assertEqual((rule_state.STATUS_FIRED, rule_state.STATUS_FIRED, (), False),
                         (again.before, again.after, again.events, again.fired))
        self.assertEqual(entry, again.entry)

    def test_next_trading_day_rearm_is_midnight_et_of_the_next_session(self) -> None:
        entry = self.fired(policy())
        cooled = run(entry, rule_state.TRUTH_FALSE, at(20, 30), value=Decimal("70"))
        self.assertEqual((rule_state.STATUS_COOLDOWN, (rule_state.EVENT_COOLDOWN,)), (cooled.after, cooled.events))
        self.assertEqual("2026-11-04T05:00:00+00:00", cooled.entry["rearm_at"], "00:00 EST is 05:00 UTC")
        still = run(cooled.entry, rule_state.TRUTH_TRUE, at(20, 45), single=True)
        self.assertEqual((rule_state.STATUS_COOLDOWN, (), False), (still.after, still.events, still.fired),
                         "TRUE again before the re-arm instant posts nothing new")
        next_day = run(still.entry, rule_state.TRUTH_TRUE, datetime.datetime(2026, 11, 4, 15, 0, tzinfo=UTC),
                       single=True, today=datetime.date(2026, 11, 4))
        self.assertEqual((rule_state.STATUS_FIRED, (rule_state.EVENT_REARMED, rule_state.EVENT_FIRED), True),
                         (next_day.after, next_day.events, next_day.fired))

    def test_next_trading_day_skips_holidays_and_injected_closures(self) -> None:
        wednesday = datetime.date(2026, 11, 25)
        self.assertEqual(datetime.datetime(2026, 11, 27, 5, 0, tzinfo=UTC),
                         rule_state.rearm_instant(rule_state.REARM_NEXT_TRADING_DAY, at(20, 0, day=25), wednesday))
        friday = datetime.date(2026, 11, 20)
        self.assertEqual(datetime.datetime(2026, 11, 23, 5, 0, tzinfo=UTC),
                         rule_state.rearm_instant(rule_state.REARM_NEXT_TRADING_DAY, at(20, 0, day=20), friday))
        closure = {datetime.date(2026, 11, 23): "calendar closure"}
        self.assertEqual(datetime.datetime(2026, 11, 24, 5, 0, tzinfo=UTC),
                         rule_state.rearm_instant(rule_state.REARM_NEXT_TRADING_DAY, at(20, 0, day=20), friday, closure))
        summer = datetime.date(2026, 7, 1)
        self.assertEqual(datetime.datetime(2026, 7, 2, 4, 0, tzinfo=UTC),
                         rule_state.rearm_instant(rule_state.REARM_NEXT_TRADING_DAY,
                                                  datetime.datetime(2026, 7, 1, 20, 0, tzinfo=UTC), summer),
                         "00:00 EDT is 04:00 UTC")

    def test_immediate_rearm(self) -> None:
        entry = self.fired(policy(rearm=rule_state.REARM_IMMEDIATE))
        cooled = run(entry, rule_state.TRUTH_FALSE, at(20, 30), pol=policy(rearm=rule_state.REARM_IMMEDIATE))
        self.assertEqual((rule_state.STATUS_COOLDOWN, "2026-11-03T20:30:00+00:00"),
                         (cooled.after, cooled.entry["rearm_at"]))
        again = run(cooled.entry, rule_state.TRUTH_TRUE, at(20, 40), pol=policy(rearm=rule_state.REARM_IMMEDIATE),
                    single=True)
        self.assertEqual((rule_state.EVENT_REARMED, rule_state.EVENT_FIRED), again.events)

    def test_hysteresis_rearms_on_the_retreat(self) -> None:
        pol = policy(rearm=rule_state.REARM_HYSTERESIS, hysteresis="10", op=">=", threshold="80")
        entry = self.fired(pol)
        cooled = run(entry, rule_state.TRUTH_FALSE, at(20, 0), pol=pol, value=Decimal("72.01"))
        self.assertEqual((rule_state.STATUS_COOLDOWN, "2026-11-04T05:00:00+00:00"),
                         (cooled.after, cooled.entry["rearm_at"]), "bounded to one trading day")
        not_yet = run(cooled.entry, rule_state.TRUTH_FALSE, at(20, 10), pol=pol, value=Decimal("72.01"))
        self.assertEqual((rule_state.STATUS_COOLDOWN, ()), (not_yet.after, not_yet.events))
        retreated = run(not_yet.entry, rule_state.TRUTH_FALSE, at(20, 20), pol=pol, value=Decimal("72.00"))
        self.assertEqual((rule_state.STATUS_ARMED, (rule_state.EVENT_REARMED,)), (retreated.after, retreated.events),
                         "80 less 10 % of 80 is 72: the metric retreated")

    def test_hysteresis_on_a_ceiling_and_its_bound(self) -> None:
        pol = policy(rearm=rule_state.REARM_HYSTERESIS, hysteresis="10", op="<=", threshold="-50")
        entry = self.fired(pol)
        cooled = run(entry, rule_state.TRUTH_FALSE, at(20, 0), pol=pol, value=Decimal("-46"))
        self.assertEqual(rule_state.STATUS_COOLDOWN, cooled.after)
        retreated = run(cooled.entry, rule_state.TRUTH_FALSE, at(20, 5), pol=pol, value=Decimal("-45"))
        self.assertEqual(rule_state.STATUS_ARMED, retreated.after)
        bounded = run(cooled.entry, rule_state.TRUTH_UNKNOWN, datetime.datetime(2026, 11, 4, 5, 0, tzinfo=UTC), pol=pol,
                      value=None, today=datetime.date(2026, 11, 4))
        self.assertEqual((rule_state.STATUS_ARMED, (rule_state.EVENT_REARMED,)), (bounded.after, bounded.events),
                         "never more than one trading day in cooldown")

    def test_retreated(self) -> None:
        self.assertTrue(rule_state.retreated(Decimal("72"), ">=", Decimal("80"), Decimal("10")))
        self.assertFalse(rule_state.retreated(Decimal("72.5"), ">", Decimal("80"), Decimal("10")))
        self.assertTrue(rule_state.retreated(Decimal("-45"), "<", Decimal("-50"), Decimal("10")))
        self.assertFalse(rule_state.retreated(None, ">=", Decimal("80"), Decimal("10")))
        self.assertTrue(rule_state.retreated(False, "is_true", None, Decimal("10")), "a boolean retreats when FALSE")
        self.assertFalse(rule_state.retreated(True, "is_true", None, Decimal("10")))

    def test_unknown_rearm_mode_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            rule_state.rearm_instant("tomorrow", at(19, 0), TODAY)
        with self.assertRaises(ValueError):
            run(None, rule_state.TRUTH_TRUE, at(19, 0), pol=policy(rearm="tomorrow"))

    def test_bad_inputs_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            run(None, "maybe", at(19, 0))
        with self.assertRaises(ValueError):
            rule_state.step(None, truth=rule_state.TRUTH_TRUE, as_of=None, value=None, policy=policy(),
                            now=datetime.datetime(2026, 11, 3, 19, 0), today=TODAY, single_observation=False,
                            opened=None, rule_sha=SHA)


class ResetTest(unittest.TestCase):
    def test_a_changed_rule_sha_starts_again(self) -> None:
        entry = run(None, rule_state.TRUTH_TRUE, at(19, 50), single=True).entry
        changed = run(entry, rule_state.TRUTH_TRUE, at(19, 55), sha=OTHER_SHA)
        self.assertEqual((None, rule_state.STATUS_PENDING), (changed.before, changed.after))
        self.assertEqual((rule_state.EVENT_RESET, rule_state.EVENT_SEEN, rule_state.EVENT_PENDING), changed.events)
        self.assertEqual(OTHER_SHA, changed.entry["rule_sha"])

    def test_an_unusable_entry_starts_again(self) -> None:
        broken = dict(rule_state.new_entry(SHA, OPENED), status="sleeping")
        result = run(broken, rule_state.TRUTH_FALSE, at(19, 50))
        self.assertEqual((rule_state.EVENT_RESET, rule_state.EVENT_SEEN), result.events)


class ApplyTest(unittest.TestCase):
    def test_apply_writes_each_update_and_leaves_the_input_alone(self) -> None:
        first = run(None, rule_state.TRUTH_TRUE, at(19, 50), single=True)
        update = rule_state.update_record("AG-20261020-01", "TP80", first, truth=rule_state.TRUTH_TRUE, as_of=at(19, 31),
                                          value=Decimal("95.32"))
        self.assertEqual(("AG-20261020-01|TP80", "AG-20261020-01", "TP80", None, rule_state.STATUS_FIRED,
                          rule_state.EVENT_FIRED, "true", "2026-11-03T19:31:00+00:00", "95.32", True, True),
                         (update["key"], update["subject"], update["rule_id"], update["before"], update["after"],
                          update["event"], update["truth"], update["as_of"], update["value"], update["fired"],
                          update["first_observation"]))
        json.dumps(update)
        before: Dict[str, Any] = {}
        after = rule_state.apply(before, [update], at(19, 50))
        self.assertEqual({}, before)
        self.assertEqual(first.entry, after["AG-20261020-01|TP80"])
        unseen = run(None, rule_state.TRUTH_UNKNOWN, at(19, 50))
        record = rule_state.update_record("AG-20261022-02", "TP80", unseen, truth=rule_state.TRUTH_UNKNOWN, as_of=None,
                                          value=None)
        self.assertEqual(after, rule_state.apply(after, [record], at(19, 50)), "nothing recorded for an unseen rule")

    def test_apply_rearms_untouched_cooldowns_whose_instant_has_passed(self) -> None:
        entry = run(None, rule_state.TRUTH_TRUE, at(19, 50), single=True).entry
        cooled = run(entry, rule_state.TRUTH_FALSE, at(20, 30)).entry
        state = {"AG-1|TP80": cooled}
        self.assertEqual(rule_state.STATUS_COOLDOWN, rule_state.apply(state, [], at(23, 0))["AG-1|TP80"]["status"])
        later = rule_state.apply(state, [], datetime.datetime(2026, 11, 4, 5, 0, tzinfo=UTC))
        self.assertEqual((rule_state.STATUS_ARMED, None), (later["AG-1|TP80"]["status"], later["AG-1|TP80"]["rearm_at"]))

    def test_apply_refuses_a_malformed_update(self) -> None:
        with self.assertRaises(ValueError):
            rule_state.apply({}, [{"key": "AG-1|TP80"}], at(19, 50))


class CarryForwardTest(unittest.TestCase):
    def test_same_sha_kept_changed_reset_removed_dropped(self) -> None:
        fired = run(None, rule_state.TRUTH_TRUE, at(19, 50), single=True).entry
        state = {"AG-1|TP50": fired, "AG-1|TP80": dict(fired), "AG-1|GONE": dict(fired)}
        outcome = rule_state.carry_forward(state, {"TP50": OTHER_SHA, "TP80": SHA})
        self.assertEqual((["AG-1|TP80"], ["AG-1|TP50"], ["AG-1|GONE"]), (outcome.kept, outcome.reset, outcome.dropped))
        self.assertEqual(fired, outcome.state["AG-1|TP80"])
        # A changed rule is started again exactly as step's own reset starts it: it leaves the state, so its next
        # observation is a first observation (the lens-1 review: both resets give the same first-observation stamp).
        self.assertNotIn("AG-1|TP50", outcome.state)
        self.assertNotIn("AG-1|GONE", outcome.state)
        self.assertEqual(3, len(state), "the input is not changed")


class PostTest(unittest.TestCase):
    def test_record_post_and_cooldown_between_posts(self) -> None:
        fired = run(None, rule_state.TRUTH_TRUE, at(19, 50), single=True).entry
        state = {"AG-1|TP80": fired}
        self.assertTrue(rule_state.may_post(fired, at(19, 50), 240))
        posted = rule_state.record_post(state, "AG-1|TP80", at(19, 50))
        entry = posted["AG-1|TP80"]
        self.assertEqual((1, "2026-11-03T19:50:00+00:00"), (entry["posts"], entry["last_posted_at"]))
        self.assertEqual(0, state["AG-1|TP80"]["posts"], "the input is not changed")
        self.assertFalse(rule_state.may_post(entry, at(23, 49), 240))
        self.assertTrue(rule_state.may_post(entry, at(23, 50), 240))
        self.assertTrue(rule_state.may_post(entry, at(19, 50), 0))
        again = rule_state.record_post(posted, "AG-1|TP80", at(23, 50))
        self.assertEqual(2, again["AG-1|TP80"]["posts"])
        with self.assertRaises(KeyError):
            rule_state.record_post(state, "AG-1|NOPE", at(19, 50))


class FileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-rule-state-")
        self.root = os.path.realpath(self._tmp.name)
        self.paths = desk_paths.for_root(self.root)
        desk_paths.ensure_runtime_dirs(self.paths)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, text: str) -> None:
        with open(self.paths.rule_state_json, "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_missing_file_is_an_empty_state(self) -> None:
        self.assertEqual(({}, rule_state.LOAD_MISSING), rule_state.load_with_status(self.paths))
        self.assertEqual({}, rule_state.load(self.paths))

    def test_round_trip_through_the_state_file(self) -> None:
        entry = run(None, rule_state.TRUTH_TRUE, at(19, 50), single=True).entry
        path = rule_state.save(self.paths, {"AG-1|TP80": entry})
        self.assertEqual(self.paths.rule_state_json, path)
        with open(path, encoding="utf-8") as handle:
            on_disk = json.load(handle)
        self.assertEqual({"schema_version": 1, "entries": {"AG-1|TP80": entry}}, on_disk)
        self.assertEqual(({"AG-1|TP80": entry}, rule_state.LOAD_OK), rule_state.load_with_status(self.paths))

    def test_corrupt_file_is_quarantined_and_reads_empty(self) -> None:
        self.write("{not json")
        self.assertEqual(({}, rule_state.LOAD_CORRUPT), rule_state.load_with_status(self.paths))
        self.assertFalse(os.path.exists(self.paths.rule_state_json))
        moved = [name for name in os.listdir(os.path.dirname(self.paths.rule_state_json)) if ".corrupt-" in name]
        self.assertEqual(1, len(moved))

    def test_wrong_shape_reads_empty(self) -> None:
        self.write(json.dumps({"schema_version": 2, "entries": {}}))
        self.assertEqual(({}, rule_state.LOAD_MALFORMED), rule_state.load_with_status(self.paths))
        self.write(json.dumps(["not", "a", "state"]))
        self.assertEqual(({}, rule_state.LOAD_MALFORMED), rule_state.load_with_status(self.paths))

    def test_a_bad_entry_is_dropped_and_reported(self) -> None:
        good = rule_state.new_entry(SHA, OPENED)
        self.write(json.dumps({"schema_version": 1, "entries": {"AG-1|TP80": good,
                                                                "AG-1|TP50": dict(good, status="sleeping"),
                                                                "no-separator": good}}))
        self.assertEqual(({"AG-1|TP80": good}, rule_state.LOAD_PARTIAL), rule_state.load_with_status(self.paths))


class IsolationTest(unittest.TestCase):
    def test_rule_state_never_imports_exit_rules(self) -> None:
        with open(rule_state.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.update(alias.name for alias in node.names)
                imported.add(node.module or "")
        self.assertFalse({name for name in imported if "exit_rules" in name})



class ReviewFixesTest(unittest.TestCase):
    """The lens-1 review: an M or G observation carries its quote stamp (a re-run on the same quote is never a new
    confirmation, so the state machine refuses an unstamped one); a re-arm waits for ``cooldown_min`` after the last
    post; carry-forward resets a changed rule the way :func:`rule_state.step` does."""

    def test_an_unstamped_m_or_g_true_observation_is_refused(self) -> None:
        for input_class in ("M", "G"):
            with self.subTest(input_class=input_class):
                with self.assertRaises(ValueError):
                    rule_state.step(None, truth=rule_state.TRUTH_TRUE, as_of=None, value=Decimal("2.05"),
                                    policy=policy(input_class), now=at(19, 50), today=TODAY, single_observation=False,
                                    opened=OPENED, rule_sha=SHA)
        timed = rule_state.step(None, truth=rule_state.TRUTH_TRUE, as_of=None, value=13,
                                policy=policy("E", threshold="21", op="<="), now=at(19, 50), today=TODAY,
                                single_observation=False, opened=OPENED, rule_sha=SHA)
        self.assertEqual(rule_state.STATUS_FIRED, timed.after, "an E rule reads no quote: its stamp is the run")
        for truth in (rule_state.TRUTH_FALSE, rule_state.TRUTH_UNKNOWN):
            rule_state.step(None, truth=truth, as_of=None, value=None, policy=policy(), now=at(19, 50), today=TODAY,
                            single_observation=False, opened=OPENED, rule_sha=SHA)

    def test_a_rearm_waits_for_cooldown_min_after_the_last_post(self) -> None:
        pol = dict(policy(rearm=rule_state.REARM_IMMEDIATE), cooldown_min=240)
        fired = run(None, rule_state.TRUTH_TRUE, at(19, 50), pol=pol, single=True).entry
        posted = rule_state.record_post({"AG-1|TP80": fired}, "AG-1|TP80", at(19, 50))["AG-1|TP80"]
        cooled = run(posted, rule_state.TRUTH_FALSE, at(20, 0), pol=pol)
        self.assertEqual((rule_state.STATUS_COOLDOWN, "2026-11-03T23:50:00+00:00"), (cooled.after, cooled.entry["rearm_at"]))
        early = run(cooled.entry, rule_state.TRUTH_TRUE, at(20, 10), pol=pol, single=True)
        self.assertEqual((rule_state.STATUS_COOLDOWN, False), (early.after, early.fired))
        late = run(cooled.entry, rule_state.TRUTH_TRUE, at(23, 55), pol=pol, single=True)
        self.assertEqual((rule_state.STATUS_FIRED, True), (late.after, late.fired))
        unposted = run(fired, rule_state.TRUTH_FALSE, at(20, 0), pol=pol)
        self.assertEqual("2026-11-03T20:00:00+00:00", unposted.entry["rearm_at"], "never posted: the mode's instant")
        without = run(posted, rule_state.TRUTH_FALSE, at(20, 0), pol=policy(rearm=rule_state.REARM_IMMEDIATE))
        self.assertEqual("2026-11-03T20:00:00+00:00", without.entry["rearm_at"], "no cooldown_min in the policy: 0")

    def test_a_hysteresis_retreat_rearms_only_after_cooldown_min(self) -> None:
        pol = dict(policy(rearm=rule_state.REARM_HYSTERESIS, hysteresis="10", op=">=", threshold="80"), cooldown_min=240)
        fired = run(None, rule_state.TRUTH_TRUE, at(19, 50), pol=pol, single=True).entry
        posted = rule_state.record_post({"AG-1|TP80": fired}, "AG-1|TP80", at(19, 50))["AG-1|TP80"]
        cooled = run(posted, rule_state.TRUTH_FALSE, at(20, 0), value=Decimal("75"), pol=pol).entry
        soon = run(cooled, rule_state.TRUTH_FALSE, at(20, 10), value=Decimal("70"), pol=pol)
        self.assertEqual((rule_state.STATUS_COOLDOWN, ()), (soon.after, soon.events), "retreated, but inside cooldown_min")
        later = run(cooled, rule_state.TRUTH_FALSE, at(23, 55), value=Decimal("70"), pol=pol)
        self.assertEqual((rule_state.STATUS_ARMED, (rule_state.EVENT_REARMED,)), (later.after, later.events))

    def test_policy_carries_cooldown_min(self) -> None:
        self.assertIn("cooldown_min", rule_state.POLICY_OPTIONAL_KEYS)
        with self.assertRaises(ValueError):
            run(None, rule_state.TRUTH_TRUE, at(19, 50), pol=dict(policy(), cooldown_min=-1))



class CooldownCapTest(unittest.TestCase):
    """``cooldown_min`` holds a re-arm back from the last post, but never past the session open of the first trading
    day after the post's ET date (the calendar's closures skipped): a cooldown of up to 1440 minutes must not keep an
    exit rule silent into the middle of the next session (the re-arm bound of one trading day, in its safe
    direction). Tuesday 2026-11-03 is EST: 15:35 ET is 20:35Z and Wednesday's 09:30 ET open is 14:30Z."""

    def posted_then_cleared(self, pol: Dict[str, Any], extra: Any = (), value: Any = Decimal("95.32")) -> Dict[str, Any]:
        fired = run(None, rule_state.TRUTH_TRUE, at(20, 35), pol=pol, single=True, extra=extra).entry
        posted = rule_state.record_post({"AG-1|TP80": fired}, "AG-1|TP80", at(20, 35))["AG-1|TP80"]
        cleared = run(posted, rule_state.TRUTH_FALSE, at(20, 50), pol=pol, value=value, extra=extra)
        self.assertEqual(rule_state.STATUS_COOLDOWN, cleared.after)
        return cleared.entry

    def test_a_long_cooldown_rearms_at_the_next_session_open(self) -> None:
        pol = dict(policy(), cooldown_min=1440)
        cooled = self.posted_then_cleared(pol)
        self.assertEqual("2026-11-04T14:30:00+00:00", cooled["rearm_at"],
                         "the later of 00:00 ET and post + 1440 min, capped at Wednesday's 09:30 ET open")
        again = run(cooled, rule_state.TRUTH_TRUE, at(15, 5, day=4), pol=pol, single=True, today=datetime.date(2026, 11, 4))
        self.assertEqual((rule_state.STATUS_FIRED, True), (again.after, again.fired),
                         "TRUE again at the 10:05 ET run of the next trading day: a new signal")
        before_open = run(cooled, rule_state.TRUTH_TRUE, at(14, 0, day=4), pol=pol, single=True,
                          today=datetime.date(2026, 11, 4))
        self.assertEqual((rule_state.STATUS_COOLDOWN, False), (before_open.after, before_open.fired))

    def test_a_short_cooldown_is_unchanged(self) -> None:
        cooled = self.posted_then_cleared(dict(policy(), cooldown_min=240))
        self.assertEqual("2026-11-04T05:00:00+00:00", cooled["rearm_at"], "00:00 ET Wednesday: the mode's instant")

    def test_an_immediate_rearm_is_capped_at_the_next_open(self) -> None:
        cooled = self.posted_then_cleared(dict(policy(rearm=rule_state.REARM_IMMEDIATE), cooldown_min=1440))
        self.assertEqual("2026-11-04T14:30:00+00:00", cooled["rearm_at"])

    def test_the_cap_skips_a_calendar_closure(self) -> None:
        closed = (datetime.date(2026, 11, 4),)
        pol = dict(policy(rearm=rule_state.REARM_IMMEDIATE), cooldown_min=1440)
        cooled = self.posted_then_cleared(pol, extra=closed)
        self.assertEqual("2026-11-04T20:35:00+00:00", cooled["rearm_at"],
                         "Wednesday closed: the next open is Thursday's, so post + 1440 min stands")

    def test_a_hysteresis_retreat_rearms_after_the_next_open(self) -> None:
        pol = dict(policy(rearm=rule_state.REARM_HYSTERESIS, hysteresis="10", op=">=", threshold="80"),
                   cooldown_min=1440)
        cooled = self.posted_then_cleared(pol, value=Decimal("75"))
        wednesday = datetime.date(2026, 11, 4)
        early = run(cooled, rule_state.TRUTH_FALSE, at(14, 0, day=4), value=Decimal("70"), pol=pol, today=wednesday)
        self.assertEqual(rule_state.STATUS_COOLDOWN, early.after, "retreated, but before the next open")
        later = run(cooled, rule_state.TRUTH_FALSE, at(14, 35, day=4), value=Decimal("70"), pol=pol, today=wednesday)
        self.assertEqual((rule_state.STATUS_ARMED, (rule_state.EVENT_REARMED,)), (later.after, later.events),
                         "retreated after the open: re-armed although post + 1440 min is still ahead")


if __name__ == "__main__":
    unittest.main()
