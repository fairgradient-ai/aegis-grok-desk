"""Strict ``desk.toml`` reader (``aegis_desk.desk_config``).

The two shipped examples load (``tests/fixtures/toml/good/desk.toml`` and the fixture desk root's
``desk.toml``); every rejection in the brief is watched failing on a one-line mutation of a valid
document, with the message naming the file, the key and the section.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import datetime
import hashlib
import os
import tempfile
import unittest
from decimal import Decimal

from aegis_desk import desk_config

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GOOD_DESK = os.path.join(ROOT, "tests", "fixtures", "toml", "good", "desk.toml")
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk", "desk.toml")

BASE = '''[desk]
timezone = "America/New_York"
engagement = "active-owner"
cadence_profile = "lean"
chatty_or_quiet = "quiet"

[owner]
broker_exercise_cutoff_et = 16:30:00
approval_level = 2
declared_equity = 250000.00
attention_thresholds = { pl_day_pct = 1.5, pl_trade_abs = 500.00 }
disclosures_acknowledged = ["occ-characteristics-and-risks-2024", "ai-role-disclosure"]

[[owner.accounts]]
name = "Brokerage (margin)"
type = "margin"

[market_data]
vendor = "tradier"
host = "sandbox"
token_env = "TRADIER_API_TOKEN"
feed_delay_min = 15
quote_ttl_min = 5
grace_min = 5
pin_band = 0.25

[rates]
risk_free_rate = 0.0412
source = "3-month Treasury bill, owner-entered"
as_of = 2026-10-30

[levels]
order_shape_enabled = false
'''


class DeskConfigTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-cfg-")
        self.dir = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, text: str, name: str = "desk.toml") -> str:
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return path

    def mutate(self, old: str, new: str, base: str = BASE) -> str:
        self.assertIn(old, base, "the mutation must change the base document")
        return base.replace(old, new, 1)

    def load_text(self, text: str) -> "desk_config.DeskConfig":
        return desk_config.load_desk_config(self.write(text))

    def assert_rejected(self, text: str, *needles: str) -> "desk_config.DeskConfigError":
        path = self.write(text)
        with self.assertRaises(desk_config.DeskConfigError) as ctx:
            desk_config.load_desk_config(path)
        message = str(ctx.exception)
        self.assertIn(path, message)
        self.assertEqual(path, ctx.exception.path)
        self.assertNotIn("\n", message)  # one line
        for needle in needles:
            self.assertIn(needle, message, message)
        self.assertIsInstance(ctx.exception, ValueError)
        return ctx.exception


class LoadsValidDocumentsTest(DeskConfigTestCase):
    def test_good_fixture_loads_with_typed_values(self) -> None:
        cfg = desk_config.load_desk_config(GOOD_DESK)
        self.assertEqual(GOOD_DESK, cfg.path)
        with open(GOOD_DESK, "rb") as handle:
            self.assertEqual(hashlib.sha256(handle.read()).hexdigest(), cfg.sha256)
        self.assertEqual(64, len(cfg.sha256))
        self.assertEqual("America/New_York", cfg.desk.timezone)
        self.assertEqual("active-owner", cfg.desk.engagement)
        self.assertEqual("lean", cfg.desk.cadence_profile)
        self.assertEqual("quiet", cfg.desk.chatty_or_quiet)
        self.assertEqual(datetime.time(16, 30), cfg.owner.broker_exercise_cutoff_et)
        self.assertEqual(2, cfg.owner.approval_level)
        self.assertEqual(Decimal("250000.00"), cfg.owner.declared_equity)
        self.assertEqual("250000.00", str(cfg.owner.declared_equity))
        self.assertEqual(Decimal("1.5"), cfg.owner.attention_thresholds.pl_day_pct)
        self.assertEqual(Decimal("500.00"), cfg.owner.attention_thresholds.pl_trade_abs)
        self.assertEqual(["occ-characteristics-and-risks-2024", "ai-role-disclosure"], cfg.owner.disclosures_acknowledged)
        self.assertEqual(2, len(cfg.owner.accounts))
        self.assertEqual(desk_config.AccountEntry("Brokerage (margin)", "margin"), cfg.owner.accounts[0])
        self.assertEqual(desk_config.AccountEntry("Roth IRA", "ira"), cfg.owner.accounts[1])
        self.assertEqual("tradier", cfg.market_data.vendor)
        self.assertEqual("sandbox", cfg.market_data.host)
        self.assertEqual("TRADIER_API_TOKEN", cfg.market_data.token_env)
        self.assertEqual("/workspace/.aegis-secrets/tradier.token", cfg.market_data.token_file)
        self.assertEqual(15, cfg.market_data.feed_delay_min)
        self.assertEqual(5, cfg.market_data.quote_ttl_min)
        self.assertEqual(5, cfg.market_data.grace_min)
        self.assertEqual(Decimal("0.25"), cfg.market_data.pin_band)
        self.assertEqual(Decimal("0.0412"), cfg.rates.risk_free_rate)
        self.assertEqual(0.0412, cfg.rates.risk_free_rate_f)
        self.assertIsInstance(cfg.rates.risk_free_rate_f, float)
        self.assertEqual("3-month Treasury bill, owner-entered", cfg.rates.source)
        self.assertEqual(datetime.date(2026, 10, 30), cfg.rates.as_of)
        self.assertNotIsInstance(cfg.rates.as_of, datetime.datetime)
        self.assertIs(False, cfg.levels.order_shape_enabled)
        self.assertEqual(25, desk_config.freshness_bound_min(cfg))

    def test_fixture_desk_root_loads(self) -> None:
        cfg = desk_config.load_desk_config(FIXTURE_DESK)
        self.assertEqual("sandbox", cfg.market_data.host)
        self.assertEqual(15, cfg.market_data.feed_delay_min)
        self.assertEqual(5, cfg.market_data.quote_ttl_min)
        self.assertEqual(5, cfg.market_data.grace_min)
        self.assertEqual(25, desk_config.freshness_bound_min(cfg))
        self.assertEqual("America/New_York", cfg.desk.timezone)
        self.assertGreater(cfg.owner.declared_equity, 0)
        self.assertGreaterEqual(len(cfg.owner.accounts), 1)
        self.assertIs(False, cfg.levels.order_shape_enabled)
        self.assertEqual(datetime.time(16, 30), cfg.owner.broker_exercise_cutoff_et)

    def test_base_document_loads_and_defaults_apply(self) -> None:
        cfg = self.load_text(BASE)
        self.assertEqual("/workspace/.aegis-secrets/tradier.token", cfg.market_data.token_file)
        self.assertEqual(desk_config.DEFAULT_TOKEN_FILE, cfg.market_data.token_file)
        cfg = self.load_text(self.mutate('token_env = "TRADIER_API_TOKEN"\n', ''))
        self.assertEqual("TRADIER_API_TOKEN", cfg.market_data.token_env)
        self.assertEqual(desk_config.DEFAULT_TOKEN_ENV, cfg.market_data.token_env)
        cfg = self.load_text(self.mutate('token_env = "TRADIER_API_TOKEN"\n',
                                         'token_env = "MY_TOKEN"\ntoken_file = "/tmp/x.token"\n'))
        self.assertEqual("MY_TOKEN", cfg.market_data.token_env)
        self.assertEqual("/tmp/x.token", cfg.market_data.token_file)

    def test_config_is_frozen(self) -> None:
        cfg = self.load_text(BASE)
        with self.assertRaises(Exception):
            cfg.desk = None  # type: ignore[misc]
        with self.assertRaises(Exception):
            cfg.market_data.host = "production"  # type: ignore[misc]

    def test_integers_accepted_where_decimals_are_expected(self) -> None:
        cfg = self.load_text(self.mutate("declared_equity = 250000.00", "declared_equity = 250000"))
        self.assertEqual(Decimal("250000"), cfg.owner.declared_equity)
        self.assertIsInstance(cfg.owner.declared_equity, Decimal)
        cfg = self.load_text(self.mutate("pin_band = 0.25", "pin_band = 0"))
        self.assertEqual(Decimal("0"), cfg.market_data.pin_band)
        cfg = self.load_text(self.mutate("risk_free_rate = 0.0412", "risk_free_rate = 0"))
        self.assertEqual(Decimal("0"), cfg.rates.risk_free_rate)
        self.assertEqual(0.0, cfg.rates.risk_free_rate_f)

    def test_production_host_is_accepted_by_the_reader(self) -> None:
        cfg = self.load_text(self.mutate('host = "sandbox"', 'host = "production"'))
        self.assertEqual("production", cfg.market_data.host)
        # Only the sandbox carries the 15-minute floor; the production feed may declare less.
        cfg = self.load_text(self.mutate('host = "sandbox"\n', 'host = "production"\n')
                             .replace("feed_delay_min = 15", "feed_delay_min = 0"))
        self.assertEqual(0, cfg.market_data.feed_delay_min)
        self.assertEqual(10, desk_config.freshness_bound_min(cfg))

    def test_active_and_chatty_and_cash_and_empty_disclosures(self) -> None:
        text = (BASE.replace('cadence_profile = "lean"', 'cadence_profile = "active"')
                .replace('chatty_or_quiet = "quiet"', 'chatty_or_quiet = "chatty"')
                .replace('type = "margin"', 'type = "cash"')
                .replace('disclosures_acknowledged = ["occ-characteristics-and-risks-2024", "ai-role-disclosure"]',
                         'disclosures_acknowledged = []')
                .replace("approval_level = 2", "approval_level = 0")
                .replace("order_shape_enabled = false", "order_shape_enabled = true"))
        cfg = self.load_text(text)
        self.assertEqual("active", cfg.desk.cadence_profile)
        self.assertEqual("chatty", cfg.desk.chatty_or_quiet)
        self.assertEqual("cash", cfg.owner.accounts[0].type)
        self.assertEqual([], cfg.owner.disclosures_acknowledged)
        self.assertEqual(0, cfg.owner.approval_level)
        self.assertIs(True, cfg.levels.order_shape_enabled)

    def test_boundaries_accepted(self) -> None:
        for old, new in (("approval_level = 2", "approval_level = 5"),
                         ("quote_ttl_min = 5", "quote_ttl_min = 1"),
                         ("quote_ttl_min = 5", "quote_ttl_min = 60"),
                         ("grace_min = 5", "grace_min = 0"),
                         ("grace_min = 5", "grace_min = 30"),
                         ("feed_delay_min = 15", "feed_delay_min = 20")):
            self.load_text(self.mutate(old, new))


class RejectionsTest(DeskConfigTestCase):
    def test_missing_file(self) -> None:
        path = os.path.join(self.dir, "absent.toml")
        with self.assertRaises(desk_config.DeskConfigError) as ctx:
            desk_config.load_desk_config(path)
        self.assertIn(path, str(ctx.exception))
        self.assertNotIsInstance(ctx.exception, FileNotFoundError)

    def test_toml_syntax_error_names_the_file(self) -> None:
        self.assert_rejected(BASE + "\nbroken = \n", "line")

    def test_invalid_utf8(self) -> None:
        path = os.path.join(self.dir, "desk.toml")
        with open(path, "wb") as handle:
            handle.write(b"[desk]\nx = \xff\n")
        with self.assertRaises(desk_config.DeskConfigError) as ctx:
            desk_config.load_desk_config(path)
        self.assertIn(path, str(ctx.exception))

    def test_unknown_key_names_key_and_section(self) -> None:
        self.assert_rejected(self.mutate('chatty_or_quiet = "quiet"\n', 'chatty_or_quiet = "quiet"\nextra = 1\n'),
                             "unknown key", "extra", "[desk]")
        self.assert_rejected(self.mutate("pin_band = 0.25\n", "pin_band = 0.25\nretries = 3\n"),
                             "unknown key", "retries", "[market_data]")
        self.assert_rejected(self.mutate('type = "margin"\n', 'type = "margin"\nnumber = "123"\n'),
                             "unknown key", "number", "[[owner.accounts]]")
        self.assert_rejected(self.mutate("pl_trade_abs = 500.00 }", "pl_trade_abs = 500.00, pl_week_pct = 3 }"),
                             "unknown key", "pl_week_pct", "attention_thresholds")

    def test_unknown_section_and_top_level_key(self) -> None:
        self.assert_rejected(BASE + "\n[extra]\nx = 1\n", "unknown section", "[extra]")
        self.assert_rejected("title = \"desk\"\n" + BASE, "unknown", "title")

    def test_machine_role_anywhere_is_refused(self) -> None:
        message = "machine_role is derived from the environment, never configured"
        self.assert_rejected('machine_role = "shared"\n' + BASE, message)
        self.assert_rejected(self.mutate('chatty_or_quiet = "quiet"\n', 'chatty_or_quiet = "quiet"\nmachine_role = "shared"\n'),
                             message)
        self.assert_rejected(self.mutate("pin_band = 0.25\n", 'pin_band = 0.25\nmachine_role = "owner_private"\n'),
                             message)
        self.assert_rejected(self.mutate("pl_trade_abs = 500.00 }", 'pl_trade_abs = 500.00, machine_role = "x" }'),
                             message)
        self.assert_rejected(BASE + '\n[machine_role]\nkind = "shared"\n', message)

    def test_timezone_must_be_new_york(self) -> None:
        self.assert_rejected(self.mutate('timezone = "America/New_York"', 'timezone = "America/Chicago"'),
                             "timezone", "America/New_York", "[desk]")
        self.assert_rejected(self.mutate('timezone = "America/New_York"', 'timezone = "US/Eastern"'), "timezone")
        self.assert_rejected(self.mutate('timezone = "America/New_York"', 'timezone = "america/new_york"'), "timezone")

    def test_enumerations(self) -> None:
        self.assert_rejected(self.mutate('cadence_profile = "lean"', 'cadence_profile = "weekly"'),
                             "cadence_profile", "lean", "active")
        self.assert_rejected(self.mutate('chatty_or_quiet = "quiet"', 'chatty_or_quiet = "loud"'), "chatty_or_quiet")
        self.assert_rejected(self.mutate('vendor = "tradier"', 'vendor = "polygon"'), "vendor", "tradier")
        self.assert_rejected(self.mutate('host = "sandbox"', 'host = "staging"'), "host", "sandbox", "production")
        self.assert_rejected(self.mutate('type = "margin"', 'type = "crypto"'), "type", "cash", "margin", "ira")
        self.assert_rejected(self.mutate('host = "sandbox"', 'host = "Sandbox"'), "host")

    def test_feed_delay_floor_on_the_sandbox(self) -> None:
        self.assert_rejected(self.mutate("feed_delay_min = 15", "feed_delay_min = 14"), "feed_delay_min", "15", "sandbox")
        self.assert_rejected(self.mutate("feed_delay_min = 15", "feed_delay_min = 0"), "feed_delay_min")
        self.assert_rejected(self.mutate("feed_delay_min = 15", "feed_delay_min = -1"), "feed_delay_min")
        self.assert_rejected(self.mutate("feed_delay_min = 15", "feed_delay_min = 15.0"), "feed_delay_min", "integer")
        self.assert_rejected(self.mutate("feed_delay_min = 15", "feed_delay_min = true"), "feed_delay_min")
        self.assert_rejected(self.mutate("feed_delay_min = 15", 'feed_delay_min = "15"'), "feed_delay_min")

    def test_other_market_data_ranges(self) -> None:
        self.assert_rejected(self.mutate("quote_ttl_min = 5", "quote_ttl_min = 0"), "quote_ttl_min", "1", "60")
        self.assert_rejected(self.mutate("quote_ttl_min = 5", "quote_ttl_min = 61"), "quote_ttl_min")
        self.assert_rejected(self.mutate("grace_min = 5", "grace_min = 31"), "grace_min", "0", "30")
        self.assert_rejected(self.mutate("grace_min = 5", "grace_min = -1"), "grace_min")
        self.assert_rejected(self.mutate("pin_band = 0.25", "pin_band = -0.25"), "pin_band")
        self.assert_rejected(self.mutate("pin_band = 0.25", 'pin_band = "0.25"'), "pin_band")
        self.assert_rejected(self.mutate('token_env = "TRADIER_API_TOKEN"', 'token_env = ""'), "token_env")
        self.assert_rejected(self.mutate('token_env = "TRADIER_API_TOKEN"', 'token_env = 7'), "token_env")
        self.assert_rejected(self.mutate('token_env = "TRADIER_API_TOKEN"\n', 'token_env = "T"\ntoken_file = 1\n'), "token_file")

    def test_owner_values(self) -> None:
        self.assert_rejected(self.mutate("declared_equity = 250000.00", "declared_equity = 0"), "declared_equity", "> 0")
        self.assert_rejected(self.mutate("declared_equity = 250000.00", "declared_equity = 0.00"), "declared_equity")
        self.assert_rejected(self.mutate("declared_equity = 250000.00", "declared_equity = -5"), "declared_equity")
        self.assert_rejected(self.mutate("declared_equity = 250000.00", 'declared_equity = "250000"'), "declared_equity")
        self.assert_rejected(self.mutate("approval_level = 2", "approval_level = 6"), "approval_level", "0", "5")
        self.assert_rejected(self.mutate("approval_level = 2", "approval_level = -1"), "approval_level")
        self.assert_rejected(self.mutate("approval_level = 2", "approval_level = true"), "approval_level")
        self.assert_rejected(self.mutate("approval_level = 2", 'approval_level = "2"'), "approval_level")
        self.assert_rejected(self.mutate("approval_level = 2", "approval_level = 2.0"), "approval_level")
        self.assert_rejected(self.mutate("broker_exercise_cutoff_et = 16:30:00", 'broker_exercise_cutoff_et = "16:30"'),
                             "broker_exercise_cutoff_et", "time")
        self.assert_rejected(self.mutate("broker_exercise_cutoff_et = 16:30:00", "broker_exercise_cutoff_et = 2026-11-03T16:30:00"),
                             "broker_exercise_cutoff_et")
        self.assert_rejected(self.mutate("pl_day_pct = 1.5", 'pl_day_pct = "1.5"'), "pl_day_pct")
        self.assert_rejected(self.mutate("attention_thresholds = { pl_day_pct = 1.5, pl_trade_abs = 500.00 }",
                                         "attention_thresholds = { pl_day_pct = 1.5 }"), "pl_trade_abs", "missing")
        self.assert_rejected(self.mutate("attention_thresholds = { pl_day_pct = 1.5, pl_trade_abs = 500.00 }",
                                         "attention_thresholds = 1.5"), "attention_thresholds")
        self.assert_rejected(self.mutate('disclosures_acknowledged = ["occ-characteristics-and-risks-2024", "ai-role-disclosure"]',
                                         'disclosures_acknowledged = ["ok", 3]'), "disclosures_acknowledged")
        self.assert_rejected(self.mutate('disclosures_acknowledged = ["occ-characteristics-and-risks-2024", "ai-role-disclosure"]',
                                         'disclosures_acknowledged = "ai-role-disclosure"'), "disclosures_acknowledged")

    def test_accounts(self) -> None:
        self.assert_rejected(self.mutate('[[owner.accounts]]\nname = "Brokerage (margin)"\ntype = "margin"\n', ''),
                             "owner.accounts", "at least one")
        self.assert_rejected(self.mutate('name = "Brokerage (margin)"', 'name = ""'), "name")
        self.assert_rejected(self.mutate('name = "Brokerage (margin)"\n', ''), "name", "missing")
        self.assert_rejected(self.mutate('name = "Brokerage (margin)"', 'name = 12'), "name")
        self.assert_rejected(self.mutate('[[owner.accounts]]\nname = "Brokerage (margin)"\ntype = "margin"\n',
                                         '[owner.accounts]\nname = "Brokerage (margin)"\ntype = "margin"\n'),
                             "owner.accounts")

    def test_rates_and_levels(self) -> None:
        self.assert_rejected(self.mutate("as_of = 2026-10-30", "as_of = 2026-10-30T00:00:00"), "as_of", "date")
        self.assert_rejected(self.mutate("as_of = 2026-10-30", 'as_of = "2026-10-30"'), "as_of")
        self.assert_rejected(self.mutate("risk_free_rate = 0.0412", 'risk_free_rate = "0.0412"'), "risk_free_rate")
        self.assert_rejected(self.mutate('source = "3-month Treasury bill, owner-entered"', "source = 3"), "source")
        self.assert_rejected(self.mutate("order_shape_enabled = false", 'order_shape_enabled = "false"'),
                             "order_shape_enabled", "bool")
        self.assert_rejected(self.mutate("order_shape_enabled = false", "order_shape_enabled = 0"), "order_shape_enabled")

    def test_missing_sections_and_keys(self) -> None:
        for section in ("[desk]", "[owner]", "[market_data]", "[rates]", "[levels]"):
            start = BASE.index(section)
            end = BASE.find("\n[", start + 1)
            end = len(BASE) if end < 0 else end
            if section == "[owner]":
                end = BASE.index("[[owner.accounts]]")
                text = BASE[:start] + BASE[end:]
                text = text.replace('[[owner.accounts]]\nname = "Brokerage (margin)"\ntype = "margin"\n', '')
            else:
                text = BASE[:start] + BASE[end + 1:]
            self.assert_rejected(text, "missing section", section)
        self.assert_rejected(self.mutate('engagement = "active-owner"\n', ''), "missing key", "engagement", "[desk]")
        self.assert_rejected(self.mutate("quote_ttl_min = 5\n", ''), "missing key", "quote_ttl_min", "[market_data]")
        self.assert_rejected(self.mutate("as_of = 2026-10-30\n", ''), "missing key", "as_of", "[rates]")
        self.assert_rejected(self.mutate("order_shape_enabled = false\n", ''), "missing key", "order_shape_enabled", "[levels]")
        self.assert_rejected(self.mutate("approval_level = 2\n", ''), "missing key", "approval_level", "[owner]")

    def test_section_that_is_not_a_table(self) -> None:
        # A top-level scalar named like a section: not an unknown key, a section that is not a table.
        text = "levels = 1\n" + self.mutate("[levels]\norder_shape_enabled = false\n", "")
        self.assert_rejected(text, "section [levels]", "table")


class HelpersTest(unittest.TestCase):
    def test_constants(self) -> None:
        self.assertEqual("America/New_York", desk_config.TIMEZONE)
        self.assertEqual(("lean", "active"), desk_config.CADENCE_PROFILES)
        self.assertEqual(("chatty", "quiet"), desk_config.CHATTY_OR_QUIET)
        self.assertEqual(("cash", "margin", "ira"), desk_config.ACCOUNT_TYPES)
        self.assertEqual(("sandbox", "production"), desk_config.HOSTS)
        self.assertEqual("tradier", desk_config.VENDOR)
        self.assertEqual(15, desk_config.SANDBOX_MIN_FEED_DELAY_MIN)
        self.assertEqual("TRADIER_API_TOKEN", desk_config.DEFAULT_TOKEN_ENV)

    def test_parse_text_helper_matches_load(self) -> None:
        cfg = desk_config.parse_desk_toml_text(BASE, path="<memory>")
        self.assertEqual("<memory>", cfg.path)
        self.assertEqual(hashlib.sha256(BASE.encode("utf-8")).hexdigest(), cfg.sha256)
        self.assertEqual(25, desk_config.freshness_bound_min(cfg))
        with self.assertRaises(desk_config.DeskConfigError) as ctx:
            desk_config.parse_desk_toml_text(BASE.replace('host = "sandbox"', 'host = "x"'), path="<memory>")
        self.assertTrue(str(ctx.exception).startswith("<memory>: "))


if __name__ == "__main__":
    unittest.main()
