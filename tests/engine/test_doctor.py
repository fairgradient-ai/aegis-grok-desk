"""``aegis_desk.doctor``: the desk doctor's twelve checks (PR 2d decision D2-12), each a PASS, WARN or FAIL with a
one-line reason, pinned through the seams the decision names (the environment, ``/workspace``, the local offset, the
checkout answer, the find-spec and credential loaders) and, for the paper-key verification, against the loopback fake
Alpaca server of ``tests/engine/test_alpaca_client.py``: 200 passes (``paper key verified``), 401 and 403 fail as a key
that is not a paper key, anything else is a warning, and every request is one GET of the paper host's ``/v2/clock``
carrying both key headers.
"""
from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import ast
import dataclasses
import datetime
import os
import shutil
import sys
import tempfile
import threading
import unittest
from typing import Any, List, Optional, Set
from unittest import mock

from aegis_desk import alpaca_client, bsm, desk_config, desk_paths, doctor, heartbeat, routines, state

from tests.engine.test_alpaca_client import FakeAlpaca, static

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS = os.path.join(ROOT, "scripts")
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
REGULAR = os.path.join(FIXTURE_DESK, "fixtures", "alpaca", "regular")
UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 3, 19, 50, tzinfo=UTC)  # 14:50 ET, Tue 2026-11-03 (EST, -05:00)
TODAY = datetime.date(2026, 11, 3)
UA = "aegis-desk-tests/0.0 desk_doctor (read-only market data)"
KEY_ID = "PKFAKEPAPERKEY0000111"
SECRET = "fake-paper-secret-0000-1111-2222-3333"
CLEAN_PIN = {"git": True, "work_tree": True, "head": "971186c6" + "0" * 32, "tag": "v0.1.0",
             "tag_commit": "971186c6" + "0" * 32, "dirty": 0, "pinned": True, "reason": "release v0.1.0 at 971186c6, clean"}
DEVELOPMENT = dict(CLEAN_PIN, tag_commit=None, pinned=False, reason="development checkout (no tag)")
TEMPLATE_OK = {"ok": True, "errors": [], "skips": [], "status": "unpublished", "release": "0.1.0"}


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def fixture_config() -> desk_config.DeskConfig:
    return desk_config.load_desk_config(os.path.join(FIXTURE_DESK, "desk.toml"))


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-doctor-")
        self.base = os.path.realpath(self._tmp.name)
        self.root = os.path.join(self.base, "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def rewrite(self, rel: str, old: str, new: str) -> None:
        path = os.path.join(self.root, rel)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(old, text)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new, 1))


class CheckTableTest(unittest.TestCase):
    def test_twelve_checks_and_three_modes(self) -> None:
        self.assertEqual(((1, "release_pin"), (2, "interpreter"), (3, "holiday_table"), (4, "key_source"),
                          (5, "paper_key"), (6, "machine_role"), (7, "broker_env"), (8, "desk_files"),
                          (9, "market_status"), (10, "connector_readback"), (11, "heartbeats"), (12, "selftest")),
                         doctor.CHECKS)
        self.assertEqual({"full": tuple(range(1, 12)), "quick": (2, 3, 4, 8, 9), "selftest": (2, 12)}, doctor.MODE_CHECKS)
        self.assertEqual(("full", "quick"), doctor.RECORD_MODES)
        self.assertEqual(("PASS", "WARN", "FAIL"), doctor.STATUSES)

    def test_check_shape_worst_and_verdict(self) -> None:
        item = doctor.check(8, doctor.WARN, "research/calendar.toml missing")
        self.assertEqual({"id": "desk_files", "number": 8, "status": "WARN", "reason": "research/calendar.toml missing"},
                         item.as_dict())
        with self.assertRaises(ValueError):
            doctor.check(8, "OK", "x")
        with self.assertRaises(ValueError):
            doctor.check(13, doctor.PASS, "x")
        self.assertEqual("FAIL", doctor.worst(["PASS", "FAIL", "WARN"]))
        self.assertEqual("WARN", doctor.worst(["PASS", "WARN"]))
        self.assertEqual("PASS", doctor.worst([]))
        self.assertEqual("PASS", doctor.verdict([doctor.check(2, doctor.PASS, "a"), doctor.check(4, doctor.WARN, "b")]))
        self.assertEqual("FAIL", doctor.verdict([doctor.check(2, doctor.PASS, "a"), doctor.check(3, doctor.FAIL, "b")]))


class ReleasePinTest(unittest.TestCase):
    def test_pinned_development_and_other_reasons(self) -> None:
        self.assertEqual(("PASS", "release v0.1.0 at 971186c6, clean"),
                         self.status(doctor.release_pin_check(CLEAN_PIN, TEMPLATE_OK)))
        self.assertEqual(("WARN", "development checkout (no tag)"),
                         self.status(doctor.release_pin_check(DEVELOPMENT, TEMPLATE_OK)))
        for reason in ("the checkout has 3 uncommitted or untracked path(s)", "git is unavailable", "not a git checkout",
                       "git refuses this checkout: it is owned by another user (see SETUP)",
                       "HEAD 971186c6 is not the commit of v0.1.0 (01234567)"):
            state_ = dict(CLEAN_PIN, pinned=False, reason=reason)
            self.assertEqual(("FAIL", reason), self.status(doctor.release_pin_check(state_, TEMPLATE_OK)))

    def test_a_failing_template_check_fails_the_pin(self) -> None:
        template = {"ok": False, "errors": ["template/grok-bot.json: skills.aegis-desk-charter sha256 does not match "
                                            "skills/aegis-desk-charter/SKILL.md", "second"]}
        self.assertEqual(("FAIL", "release v0.1.0 at 971186c6, clean; template/grok-bot.json: skills.aegis-desk-charter "
                                  "sha256 does not match skills/aegis-desk-charter/SKILL.md"),
                         self.status(doctor.release_pin_check(CLEAN_PIN, template)))
        self.assertEqual(("FAIL", "development checkout (no tag); template/grok-bot.json: plugin.json: missing"),
                         self.status(doctor.release_pin_check(DEVELOPMENT, {"ok": False, "errors": ["plugin.json: missing"]})))

    def status(self, item: doctor.Check) -> Any:
        self.assertEqual((1, "release_pin"), (item.number, item.id))
        return item.status, item.reason


class InterpreterTest(unittest.TestCase):
    def test_this_interpreter_passes(self) -> None:
        item = doctor.interpreter_check()
        self.assertEqual(("PASS", 2, "interpreter"), (item.status, item.number, item.id))
        self.assertEqual("Python %d.%d.%d (floor 3.9); %d desk standard-library modules found"
                         % (sys.version_info[0], sys.version_info[1], sys.version_info[2], len(doctor.DESK_STDLIB_MODULES)),
                         item.reason)

    def test_a_missing_module_and_an_old_interpreter_fail(self) -> None:
        item = doctor.interpreter_check(find_spec=lambda name: None if name in ("fcntl", "ssl") else object())
        self.assertEqual(("FAIL", "missing standard-library module(s): fcntl, ssl"), (item.status, item.reason))

        def broken(name: str) -> Any:
            raise ValueError("no spec")

        self.assertEqual("FAIL", doctor.interpreter_check(find_spec=broken).status)
        item = doctor.interpreter_check(version_info=(3, 8, 18))
        self.assertEqual(("FAIL", "Python 3.8.18 is below the desk's floor 3.9"), (item.status, item.reason))

    def test_the_tuple_names_top_level_modules_that_exist(self) -> None:
        self.assertEqual(len(set(doctor.DESK_STDLIB_MODULES)), len(doctor.DESK_STDLIB_MODULES))
        self.assertEqual(sorted(doctor.DESK_STDLIB_MODULES), list(doctor.DESK_STDLIB_MODULES))
        for name in doctor.DESK_STDLIB_MODULES:
            self.assertNotIn(".", name)
            self.assertFalse(os.path.exists(os.path.join(SCRIPTS, name + ".py")), name)
        for name in ("argparse", "decimal", "fcntl", "subprocess", "urllib"):
            self.assertIn(name, doctor.DESK_STDLIB_MODULES)

    def test_the_tuple_covers_every_standard_library_import_of_the_desk_tools(self) -> None:
        missing = sorted(desk_import_closure() - set(doctor.DESK_STDLIB_MODULES))
        self.assertEqual([], missing, "add these top-level modules to doctor.DESK_STDLIB_MODULES")


def desk_import_closure() -> Set[str]:
    """Every top-level standard-library name the desk tools import: the tools (``scripts/*.py`` built on
    ``contract.ReadOnlyParser``) and the ``aegis_desk`` package, following imports of other ``scripts/`` modules
    (``check_grok_template``, ``exit_check``) to a fixed point. Function-level imports count too."""
    local = {name[:-3] for name in os.listdir(SCRIPTS) if name.endswith(".py")} | {"aegis_desk"}
    queue: List[str] = []
    for name in sorted(os.listdir(SCRIPTS)):
        path = os.path.join(SCRIPTS, name)
        if name.endswith(".py"):
            with open(path, encoding="utf-8") as handle:
                if "contract.ReadOnlyParser(" in handle.read():
                    queue.append(path)
    for folder, _dirs, files in os.walk(os.path.join(SCRIPTS, "aegis_desk")):
        queue.extend(os.path.join(folder, name) for name in sorted(files) if name.endswith(".py"))
    seen: Set[str] = set()
    found: Set[str] = set()
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        for node in ast.walk(tree):
            names: List[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                if top in local:
                    candidate = os.path.join(SCRIPTS, top + ".py")
                    if os.path.isfile(candidate):
                        queue.append(candidate)
                elif top != "__future__":
                    found.add(top)
    return found


class HolidayTableTest(unittest.TestCase):
    def test_ok_expiring_soon_and_expired(self) -> None:
        cases = ((datetime.date(2026, 11, 4), "PASS", "holiday table covers 2026-2027 (expires 2027-12-31)"),
                 (datetime.date(2027, 10, 1), "PASS", "holiday table covers 2026-2027 (expires 2027-12-31)"),
                 (datetime.date(2027, 11, 15), "FAIL", "holiday table expires 2027-12-31: update the release"),
                 (datetime.date(2028, 1, 5), "FAIL", "holiday table expired 2027-12-31"))
        for day, status, reason in cases:
            item = doctor.holiday_table_check(day)
            self.assertEqual((3, status, reason), (item.number, item.status, item.reason), day)


class KeySourceTest(unittest.TestCase):
    KEY_ENV = "AEGIS_DOCTOR_TEST_KEY_ID"
    SECRET_ENV = "AEGIS_DOCTOR_TEST_SECRET"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-doctor-key-")
        self.base = os.path.realpath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.cfg = fixture_config()

    def config(self, credentials_file: str) -> desk_config.DeskConfig:
        market = dataclasses.replace(self.cfg.market_data, key_env=self.KEY_ENV, secret_env=self.SECRET_ENV,
                                     credentials_file=credentials_file)
        return dataclasses.replace(self.cfg, market_data=market)

    def scrubbed(self) -> dict:
        env = dict(os.environ)
        env.pop(self.KEY_ENV, None)
        env.pop(self.SECRET_ENV, None)
        return env

    def write_file(self, mode: int) -> str:
        path = os.path.join(self.base, "alpaca.key")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(KEY_ID + "\n" + SECRET + "\n")
        os.chmod(path, mode)
        return path

    def test_offline_reads_no_credential(self) -> None:
        def refuse(key_env: str, secret_env: str, path: Optional[str]) -> Any:
            raise AssertionError("an offline doctor loads no credential")

        item, credential = doctor.key_source_check(self.cfg, True, load=refuse)
        self.assertEqual((4, "key_source", "WARN", "not checked offline (offline runs load no credential)", None),
                         (item.number, item.id, item.status, item.reason, credential))

    def test_environment_variables_and_file(self) -> None:
        with mock.patch.dict(os.environ, {self.KEY_ENV: KEY_ID, self.SECRET_ENV: SECRET}):
            item, credential = doctor.key_source_check(self.config(os.path.join(self.base, "none")), False)
        self.assertEqual(("PASS", "key pair from environment variables %s and %s" % (self.KEY_ENV, self.SECRET_ENV),
                          "env"), (item.status, item.reason, credential.source))
        path = self.write_file(0o600)
        with mock.patch.dict(os.environ, self.scrubbed(), clear=True):
            item, credential = doctor.key_source_check(self.config(path), False)
        self.assertEqual(("PASS", "key pair from file %s (no group or other permission bits)" % path, "file"),
                         (item.status, item.reason, credential.source))
        for value in (KEY_ID, SECRET):
            self.assertNotIn(value, item.reason)

    def test_a_loose_file_mode_half_a_pair_and_no_key_fail_without_a_value(self) -> None:
        path = self.write_file(0o644)
        with mock.patch.dict(os.environ, self.scrubbed(), clear=True):
            item, credential = doctor.key_source_check(self.config(path), False)
            self.assertEqual(("FAIL", None), (item.status, credential))
            self.assertIn("0644", item.reason)
            missing = os.path.join(self.base, "missing.key")
            item, credential = doctor.key_source_check(self.config(missing), False)
            self.assertEqual(("FAIL", None), (item.status, credential))
            self.assertIn(self.KEY_ENV, item.reason)
            with mock.patch.dict(os.environ, {self.KEY_ENV: KEY_ID}):
                item, credential = doctor.key_source_check(self.config(missing), False)
            self.assertEqual(("FAIL", None), (item.status, credential), "never half a pair from each source")
            self.assertIn(self.SECRET_ENV, item.reason)
            for value in (KEY_ID, SECRET):
                self.assertNotIn(value, item.reason)

    def test_without_a_configuration_nothing_is_read(self) -> None:
        item, credential = doctor.key_source_check(None, False)
        self.assertEqual(("WARN", "not checked: desk.toml cannot be used (see desk_files)", None),
                         (item.status, item.reason, credential))


class PaperKeyTest(unittest.TestCase):
    server: FakeAlpaca
    thread: threading.Thread

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = FakeAlpaca()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        with self.server.lock:
            del self.server.requests[:]
            del self.server.violations[:]
        self.server.script.clear()

    def tearDown(self) -> None:
        self.server.script.clear()
        self.assertEqual([], self.server.violations, "a non-GET request reached the fake server")

    def credential(self) -> alpaca_client.Credential:
        return alpaca_client.Credential(KEY_ID, SECRET, "env")

    def client(self) -> alpaca_client.AlpacaClient:
        return alpaca_client.AlpacaClient("loopback:%d" % self.server.port, self.credential(), user_agent=UA,
                                          timeout_s=5.0)

    def verify(self, status: int, body: bytes) -> doctor.Check:
        self.server.script[alpaca_client.PATH_CLOCK] = static(status, body)
        item = doctor.verify_paper_key(self.client())
        with self.server.lock:
            seen = list(self.server.requests)
        self.assertEqual([("GET", "/v2/clock")], [(r["method"], r["path"]) for r in seen])
        self.assertEqual((KEY_ID, SECRET), (seen[0]["headers"]["apca-api-key-id"],
                                            seen[0]["headers"]["apca-api-secret-key"]))
        self.assertEqual((5, "paper_key"), (item.number, item.id))
        return item

    def test_200_passes_as_a_verified_paper_key(self) -> None:
        item = self.verify(200, read_bytes(os.path.join(REGULAR, "clock.json")))
        self.assertEqual(("PASS", "paper key verified (paper clock answered: open)"), (item.status, item.reason))
        self.assertEqual("paper key verified", doctor.PAPER_VERIFIED_REASON)

    def test_401_and_403_fail_as_not_a_paper_key(self) -> None:
        for status in (401, 403):
            with self.subTest(status=status):
                self.setUp()
                item = self.verify(status, read_bytes(os.path.join(REGULAR, "errors", "unauthorized.txt")))
                self.assertEqual(("FAIL", "not a paper key: remove it and add a paper key"),
                                 (item.status, item.reason))
                for value in (KEY_ID, SECRET):
                    self.assertNotIn(value, item.reason)

    def test_other_answers_warn_with_the_status(self) -> None:
        item = self.verify(500, b'{"message": "boom"}')
        self.assertEqual(("WARN", "paper key not verified: http"), (item.status, item.reason))
        self.setUp()
        item = self.verify(429, b'{"message": "too many requests."}')
        self.assertEqual(("WARN", "paper key not verified: quota"), (item.status, item.reason))

    def test_a_non_paper_host_offline_and_missing_inputs_send_nothing(self) -> None:
        cfg = fixture_config()

        def refuse(credential: Any) -> Any:
            raise AssertionError("no client is built")

        live = dataclasses.replace(cfg, market_data=dataclasses.replace(cfg.market_data, host="live"))
        credential = self.credential()
        reason = "only the paper host is supported: the desk holds a paper key pair and never points at a live account"
        self.assertEqual(reason, doctor.PAPER_HOST_REASON)
        cases = ((live, credential, False, "FAIL", reason),
                 (live, credential, True, "FAIL", reason),
                 (cfg, credential, True, "WARN", "not verified offline"),
                 (cfg, None, False, "WARN", "paper key not verified: no key pair (see key_source)"),
                 (None, credential, False, "WARN", "not verified: desk.toml cannot be used (see desk_files)"))
        for config, given, offline, status, expected in cases:
            item = doctor.paper_key_check(config, given, offline, refuse)
            self.assertEqual((5, status, expected), (item.number, item.status, item.reason))
        with self.server.lock:
            self.assertEqual([], self.server.requests)

    def test_the_paper_key_check_builds_one_client_and_asks_once(self) -> None:
        self.server.script[alpaca_client.PATH_CLOCK] = static(200, read_bytes(os.path.join(REGULAR, "clock.json")))
        built: List[Any] = []

        def factory(credential: Any) -> Any:
            built.append(credential)
            return self.client()

        credential = self.credential()
        item = doctor.paper_key_check(fixture_config(), credential, False, factory)
        self.assertEqual(("PASS", [credential]), (item.status, built))
        with self.server.lock:
            self.assertEqual(1, len(self.server.requests))

    def test_the_real_client_asks_the_paper_host_never_the_data_host(self) -> None:
        client = alpaca_client.AlpacaClient("paper", self.credential(), user_agent=UA)
        self.assertEqual("paper-api.alpaca.markets", alpaca_client.host_for_path(alpaca_client.PATH_CLOCK))
        self.assertTrue(client.url_for(alpaca_client.PATH_CLOCK, {}).startswith("https://paper-api.alpaca.markets/"))


class MachineRoleAndBrokerEnvTest(unittest.TestCase):
    def test_machine_role_is_owner_private_only_by_the_environment_and_no_workspace(self) -> None:
        self.assertEqual("owner_private", doctor.machine_role({"AEGIS_MACHINE_ROLE": "owner_private"}, False))
        self.assertEqual("shared", doctor.machine_role({"AEGIS_MACHINE_ROLE": "owner_private"}, True))
        self.assertEqual("shared", doctor.machine_role({"AEGIS_MACHINE_ROLE": "OWNER_PRIVATE"}, False))
        self.assertEqual("shared", doctor.machine_role({}, False))
        item = doctor.machine_role_check("shared")
        self.assertEqual((6, "PASS", "machine role: shared"), (item.number, item.status, item.reason))

    def test_broker_looking_names(self) -> None:
        keys = ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY")
        flagged = ["SCHWAB_APP_KEY", "IBKR_TOKEN", "MY_BROKER_URL", "ORDER_API", "alpaca_key", "ALPACA_API_KEY",
                   "ALPACA_SECRET", "INTERACTIVE_BROKERS_PORT", "robinhood-session", "SNAPTRADE_CLIENT_ID",
                   "MY_ACCOUNT_ID"]
        clean = ["GCM_INTERACTIVE", "BORDER_COLOR", "RECORDER_PATH", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY",
                 "PYTHONINTERACTIVE", "HOME", "PATH", "ACCOUNT", "ID_FILE"]
        self.assertEqual(sorted(flagged), doctor.broker_env_names(flagged + clean, keys))
        self.assertEqual([], doctor.broker_env_names(clean, keys))
        self.assertEqual([], doctor.broker_env_names(["IBKR_TOKEN"], ["IBKR_TOKEN"]), "a configured key name is excluded")
        # exactly the configured names are excluded: a set, never a prefix or a vendor
        self.assertEqual(["ALPACA_KEY_ID", "ALPACA_SECRET_KEY"],
                         doctor.broker_env_names(["ALPACA_KEY_ID", "ALPACA_SECRET_KEY", "MY_KEY"], ["MY_KEY"]))
        self.assertEqual(["ALPACA_SECRET_KEY"],
                         doctor.broker_env_names(["ALPACA_KEY_ID", "ALPACA_SECRET_KEY"], {"ALPACA_KEY_ID"}))
        self.assertEqual([], doctor.broker_env_names(["ALPACA_KEY_ID", "ALPACA_SECRET_KEY"],
                                                     ("ALPACA_KEY_ID", "ALPACA_SECRET_KEY")))
        # joined, camel-case and two-part broker names, and an Alpaca key name that says live (one that can trade)
        joined = ["SCHWABAPIKEY", "schwabToken", "E_TRADE_KEY", "TD_AMERITRADE_KEY", "tdAmeritradeKey", "IB_GATEWAY_PORT",
                  "COINBASE_API_KEY", "APCA_LIVE_API_KEY_ID", "APCA_PROD_SECRET", "APCA_PRODUCTION_KEY",
                  "APCA_ACCOUNT_ID", "APCA_TRADING_KEY", "IBKRTOKEN", "ROBINHOOD_PASSWORD"]
        still_clean = ["APCA_PAPER_KEY_ID", "APCA_API_BASE_URL", "pythonInteractive", "borderColor", "recorderPath",
                       "SORTORDER", "MY_IB", "E_FILE", "IBM_HOME", "TASTYFOOD"]
        self.assertEqual(sorted(joined), doctor.broker_env_names(joined + clean + still_clean, keys))
        environ = {name: "x" for name in ["SCHWAB_APP_KEY", "HOME", "GCM_INTERACTIVE", "ORDER_API"]}
        item = doctor.broker_env_check(environ, keys)
        self.assertEqual((7, "FAIL", "broker-looking environment variable(s): ORDER_API, SCHWAB_APP_KEY"),
                         (item.number, item.status, item.reason))
        item = doctor.broker_env_check({"HOME": "/root", "GCM_INTERACTIVE": "never"}, keys)
        self.assertEqual(("PASS", "no broker-looking environment variable name (2 names checked)"),
                         (item.status, item.reason))
        self.assertNotIn("/root", item.reason, "values are never read into a reason")

    def test_configured_names_that_say_live_still_fail(self) -> None:
        # The exclusion exempts the configured names from the broker-name PART and PREFIX rules only; the pair rule
        # (an APCA name that says live, production, trading or account) still applies to them.
        live = ("APCA_LIVE_API_KEY_ID", "APCA_LIVE_API_SECRET_KEY")
        self.assertEqual(sorted(live), doctor.broker_env_names(list(live), live))
        for configured in (("APCA_PROD_KEY_ID", "APCA_PROD_SECRET"), ("APCA_TRADING_KEY", "APCA_TRADING_SECRET"),
                           ("APCA_ACCOUNT_KEY", "APCA_PRODUCTION_SECRET")):
            with self.subTest(configured=configured):
                self.assertEqual(sorted(configured), doctor.broker_env_names(list(configured), configured))
        item = doctor.broker_env_check({name: "x" for name in live + ("HOME",)}, live)
        self.assertEqual((7, "FAIL", "broker-looking environment variable(s): APCA_LIVE_API_KEY_ID, "
                                     "APCA_LIVE_API_SECRET_KEY"), (item.number, item.status, item.reason))
        # the part/prefix exemption is unchanged: configured ALPACA_* names pass, unconfigured ones fail
        self.assertEqual([], doctor.broker_env_names(["ALPACA_PAPER_KEY_ID", "APCA_API_KEY_ID"],
                                                     ("ALPACA_PAPER_KEY_ID", "APCA_API_KEY_ID")))

    def test_a_live_alpaca_base_url_fails(self) -> None:
        keys = ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY")
        self.assertEqual("APCA_API_BASE_URL", doctor.ALPACA_BASE_URL_ENV)
        for value in ("https://api.alpaca.markets", "https://API.alpaca.markets/v2", "api.alpaca.markets",
                      "https://api.alpaca.markets:443/", "http://api.alpaca.markets."):
            with self.subTest(value=value):
                self.assertTrue(doctor.live_base_url(value))
                item = doctor.broker_env_check({"APCA_API_BASE_URL": value, "HOME": "/root"}, keys)
                self.assertEqual((7, "FAIL", "APCA_API_BASE_URL points at the live trading host api.alpaca.markets"),
                                 (item.number, item.status, item.reason))
        for value in ("https://paper-api.alpaca.markets", "paper-api.alpaca.markets/v2", "", "   ", "not a url",
                      "https://api.alpaca.markets.example.com", "https://data.alpaca.markets", "http://[::1"):
            with self.subTest(value=value):
                self.assertFalse(doctor.live_base_url(value))
                item = doctor.broker_env_check({"APCA_API_BASE_URL": value}, keys)
                self.assertEqual("PASS", item.status)
        item = doctor.broker_env_check({"APCA_API_BASE_URL": "https://api.alpaca.markets", "ALPACA_KEY": "x"}, keys)
        self.assertEqual(("FAIL", "APCA_API_BASE_URL points at the live trading host api.alpaca.markets; "
                                  "broker-looking environment variable(s): ALPACA_KEY"), (item.status, item.reason))


class DeskFilesTest(DeskCopy):
    PASS_REASON = ("desk.toml loads (sha f08a18dc); positions.csv parses: 7 rows in 4 groups, none quarantined; "
                   "rules 1.0.0, sha cc09859c; research/calendar.toml loads")

    def run_check(self) -> Any:
        item, cfg, cal = doctor.desk_files_check(self.paths, TODAY)
        self.assertEqual((8, "desk_files"), (item.number, item.id))
        return item, cfg, cal

    def test_the_fixture_root_passes(self) -> None:
        item, cfg, cal = self.run_check()
        self.assertEqual(("PASS", self.PASS_REASON), (item.status, item.reason))
        self.assertEqual("lean", cfg.desk.cadence_profile)
        self.assertIsNotNone(cal)

    def test_a_live_host_fails_with_no_request(self) -> None:
        """Check 8 refuses a live host itself (desk_config refuses it at load), so the quick doctor (no check 5) agrees
        with the full one."""
        self.rewrite("desk.toml", 'host = "paper"', 'host = "live"')
        item, cfg, _cal = self.run_check()
        self.assertEqual("FAIL", item.status)
        self.assertIn('host must be "paper" (got "live")', item.reason)
        self.assertIsNone(cfg)

    def test_any_host_but_paper_fails_even_when_a_config_carries_one(self) -> None:
        loaded = desk_config.load_desk_config(self.paths.desk_toml)
        odd = dataclasses.replace(loaded, market_data=dataclasses.replace(loaded.market_data, host="live"))
        with mock.patch.object(doctor.desk_config, "load_desk_config", return_value=odd):
            item, cfg, _cal = self.run_check()
        self.assertEqual("FAIL", item.status)
        self.assertIn("desk.toml host live: " + doctor.PAPER_HOST_REASON, item.reason)
        self.assertEqual("live", cfg.market_data.host)

    def test_worst_status_wins(self) -> None:
        os.remove(self.paths.calendar_toml)
        item, _cfg, cal = self.run_check()
        self.assertEqual(("WARN", None), (item.status, cal))
        self.assertTrue(item.reason.endswith("; research/calendar.toml missing"), item.reason)
        self.rewrite("positions.csv", "-684.00,,3.42", "684.00,,3.42")
        item, _cfg, _cal = self.run_check()
        self.assertEqual("WARN", item.status)
        self.assertIn("positions.csv parses: 7 rows in 4 groups, 1 quarantined (AG-20261020-01)", item.reason)
        self.rewrite("rules/exit_rules-1.0.0.toml", "title", "title ", )
        item, _cfg, _cal = self.run_check()
        self.assertEqual("FAIL", item.status)
        self.assertIn("rules_tampered: ", item.reason)

    def test_unusable_files_fail(self) -> None:
        self.rewrite("desk.toml", "grace_min = 5", "grace_min = 31")
        item, cfg, _cal = self.run_check()
        self.assertEqual(("FAIL", None), (item.status, cfg))
        self.assertTrue(item.reason.startswith(os.path.join(self.root, "desk.toml") + ": "), item.reason)
        os.remove(self.paths.desk_toml)
        item, cfg, _cal = self.run_check()
        self.assertEqual(("FAIL", None), (item.status, cfg))
        self.rewrite("positions.csv", "cost_basis_total,", "cost_basis_totl,")
        item, _cfg, _cal = self.run_check()
        self.assertIn("positions.csv cannot be used: ", item.reason)
        os.remove(self.paths.positions_csv)
        item, _cfg, _cal = self.run_check()
        self.assertIn("positions.csv missing", item.reason)
        self.rewrite("research/calendar.toml", "schema_version = 1", "schema_version = 2")
        item, _cfg, cal = self.run_check()
        self.assertEqual(("FAIL", None), (item.status, cal))
        os.remove(self.paths.adopted_file)
        item, _cfg, _cal = self.run_check()
        self.assertIn("not_adopted: ", item.reason)


class MarketStatusTest(unittest.TestCase):
    def test_utc_and_new_york_offsets_pass(self) -> None:
        for offset in (0, -5 * 3600):
            item = doctor.market_status_check(NOW, offset)
            self.assertEqual((9, "PASS"), (item.number, item.status), item.reason)
        item = doctor.market_status_check(NOW, 0)
        self.assertEqual("session open at 14:50 ET; America/New_York offset -05:00; local offset +00:00", item.reason)
        summer = datetime.datetime(2026, 7, 1, 14, 0, tzinfo=UTC)
        self.assertEqual("PASS", doctor.market_status_check(summer, -4 * 3600).status)

    def test_another_offset_warns(self) -> None:
        item = doctor.market_status_check(NOW, 3600)
        self.assertEqual(("WARN", "local timezone offset +01:00 is neither UTC nor America/New_York (-05:00); session "
                                  "open at 14:50 ET"), (item.status, item.reason))
        summer = datetime.datetime(2026, 7, 1, 14, 0, tzinfo=UTC)
        item = doctor.market_status_check(summer, -5 * 3600)
        self.assertEqual(("WARN", "local timezone offset -05:00 is neither UTC nor America/New_York (-04:00); session "
                                  "open at 10:00 ET"), (item.status, item.reason))
        self.assertEqual("+05:30", doctor.format_offset(19800))
        self.assertEqual("-09:30", doctor.format_offset(-34200))

    def test_closures_and_weekends_are_named(self) -> None:
        item = doctor.market_status_check(NOW, 0, {TODAY: "Owner closure"})
        self.assertTrue(item.reason.startswith("session holiday at 14:50 ET"), item.reason)
        saturday = datetime.datetime(2026, 11, 7, 15, 0, tzinfo=UTC)
        self.assertTrue(doctor.market_status_check(saturday, 0).reason.startswith("session weekend at 10:00 ET"))


class ConnectorReadbackTest(DeskCopy):
    def write_state(self, document: Any) -> None:
        state.atomic_write_json(self.paths.desk_state_json, document)

    def test_missing_old_and_fresh_records(self) -> None:
        reason = "re-confirm connected apps and MCP servers"
        item = doctor.connector_readback_check(self.paths, NOW)
        self.assertEqual((10, "WARN", reason + " (state/desk_state.json records no connector_readback_utc)"),
                         (item.number, item.status, item.reason))
        self.write_state({"acknowledged_hashes": {}})
        self.assertEqual("WARN", doctor.connector_readback_check(self.paths, NOW).status)
        self.write_state({"connector_readback_utc": "2026-10-03T19:49:59Z"})
        item = doctor.connector_readback_check(self.paths, NOW)
        self.assertEqual(("WARN", reason + " (last confirmed 2026-10-03, 31 days ago)"), (item.status, item.reason))
        self.write_state({"connector_readback_utc": "2026-10-04T19:50:00Z"})
        item = doctor.connector_readback_check(self.paths, NOW)
        self.assertEqual(("PASS", "connected apps and MCP servers confirmed 2026-10-04 (30 days ago)"),
                         (item.status, item.reason))
        self.write_state({"connector_readback_utc": "2026-11-04T00:00:00Z"})
        item = doctor.connector_readback_check(self.paths, NOW)
        self.assertEqual(("WARN", reason + " (connector_readback_utc 2026-11-04T00:00:00Z is after the run instant)"),
                         (item.status, item.reason))
        self.write_state({"connector_readback_utc": "yesterday"})
        item = doctor.connector_readback_check(self.paths, NOW)
        self.assertEqual(("WARN", reason + " (state/desk_state.json connector_readback_utc is not an ISO-8601 UTC instant)"),
                         (item.status, item.reason))


class HeartbeatsTest(DeskCopy):
    def setUp(self) -> None:
        super().setUp()
        self.manifest = routines.load_manifest()

    def beat(self, routine_id: str, slot: str, run_utc: str) -> None:
        moment = datetime.datetime.strptime(run_utc, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)
        heartbeat.write_heartbeat(self.paths, routine_id, now=moment, exit_code=0, status="ok", started_utc=moment,
                                  finished_utc=moment, message_sha256=None, scheduled_et=slot, tool_version="0.1.0")

    def test_missed_slots_warn_by_name(self) -> None:
        self.beat("preopen", "07:30", "2026-11-03T12:31Z")
        self.beat("book-check", "10:05", "2026-11-03T15:06Z")
        item = doctor.heartbeats_check(self.paths, self.manifest, "lean", NOW)
        self.assertEqual((11, "WARN", "book-check 12:05 ET: no heartbeat 45 min after the slot; book-check 14:05 ET: no "
                                      "heartbeat 45 min after the slot"), (item.number, item.status, item.reason))
        before = NOW - datetime.timedelta(minutes=1)  # 14:49 ET: the 14:05 slot is not 45 min old yet
        item = doctor.heartbeats_check(self.paths, self.manifest, "lean", before)
        self.assertEqual("book-check 12:05 ET: no heartbeat 45 min after the slot", item.reason)

    def test_every_due_slot_ran(self) -> None:
        for routine_id, slot, run in (("preopen", "07:30", "2026-11-03T12:31Z"), ("book-check", "10:05", "2026-11-03T15:06Z"),
                                      ("book-check", "12:05", "2026-11-03T17:06Z"), ("book-check", "14:05", "2026-11-03T19:06Z")):
            self.beat(routine_id, slot, run)
        item = doctor.heartbeats_check(self.paths, self.manifest, "lean", NOW)
        self.assertEqual(("PASS", "4 of 4 due slots ran today (profile lean); latest heartbeats: book-check 44 min ago, "
                                  "preopen 439 min ago"), (item.status, item.reason))

    def test_nothing_recorded_before_the_first_slot(self) -> None:
        early = datetime.datetime(2026, 11, 3, 12, 0, tzinfo=UTC)  # 07:00 ET
        item = doctor.heartbeats_check(self.paths, self.manifest, "lean", early)
        self.assertEqual(("PASS", "no slot due yet today (profile lean); no heartbeat recorded yet"),
                         (item.status, item.reason))
        saturday = datetime.datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
        item = doctor.heartbeats_check(self.paths, self.manifest, "lean", saturday)
        self.assertEqual("PASS", item.status)


class SelftestTest(unittest.TestCase):
    def test_every_sub_check_passes(self) -> None:
        self.assertEqual([(name, True) for name in doctor.SELFTEST_NAMES], doctor.selftest_results())
        item = doctor.selftest_check()
        self.assertEqual((12, "PASS", "engine selftest: 5 of 5 sub-checks passed (bsm golden, iv round trip, DST "
                                      "transitions, TOML reader, alert lint)"), (item.number, item.status, item.reason))

    def test_a_broken_engine_fails_by_name(self) -> None:
        with mock.patch.object(bsm, "price", return_value=13.5):
            item = doctor.selftest_check()
        self.assertEqual(("FAIL", "engine selftest: bsm golden, iv round trip"), (item.status, item.reason))
        with mock.patch.object(doctor.toml_compat, "loads", side_effect=ValueError("broken reader")):
            item = doctor.selftest_check()
        self.assertEqual(("FAIL", "engine selftest: TOML reader"), (item.status, item.reason))
        with mock.patch.object(doctor.alert_lint, "check", return_value=doctor.alert_lint.LintResult(True, [])):
            item = doctor.selftest_check()
        self.assertEqual(("FAIL", "engine selftest: alert lint"), (item.status, item.reason),
                         "a lint that passes the message with nothing emitted is broken")

    def test_the_golden_is_the_suite_s_golden_of_record(self) -> None:
        self.assertEqual(13.494231234329277, doctor.SELFTEST_BSM_GOLDEN)
        self.assertEqual(("CALL", 162.06, 170.163, 1.553, 0.05, 0.052, 0.226), doctor.SELFTEST_BSM_ARGS)


class RecordTest(DeskCopy):
    def test_the_record_shape(self) -> None:
        checks = [doctor.check(2, doctor.PASS, "a"), doctor.check(4, doctor.WARN, "b")]
        record = doctor.doctor_record("quick", NOW, checks, "shared", "0.1.0")
        self.assertEqual({"schema": 1, "run_utc": "2026-11-03T19:50:00+00:00", "mode": "quick", "verdict": "PASS",
                          "checks": [{"id": "interpreter", "number": 2, "status": "PASS", "reason": "a"},
                                     {"id": "key_source", "number": 4, "status": "WARN", "reason": "b"}],
                          "machine_role": "shared", "tool_version": "0.1.0"}, record)
        self.assertEqual(os.path.join(self.root, "state", "doctor.json"), doctor.doctor_path(self.paths))
        self.assertEqual("doctor.json", doctor.DOCTOR_FILE)


if __name__ == "__main__":
    unittest.main()
