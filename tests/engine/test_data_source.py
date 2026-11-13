"""``aegis_desk.data_source``: the one source label and the words every quote line prints."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import ast
import os
import re
import unittest

from aegis_desk import alert_lint, data_source, desk_config

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ConstantsTest(unittest.TestCase):
    def test_label_and_config_agree(self) -> None:
        self.assertEqual("alpaca:paper", data_source.LABEL)
        self.assertEqual(desk_config.VENDOR, data_source.VENDOR)
        self.assertEqual(desk_config.HOSTS, (data_source.HOST,))
        self.assertEqual(desk_config.OPTIONS_FEED_INDICATIVE, data_source.OPTIONS_FEED_INDICATIVE)
        self.assertEqual(desk_config.OPTIONS_FEED_OPRA, data_source.OPTIONS_FEED_OPRA)
        self.assertEqual(desk_config.DEFAULT_OPTIONS_FEED, data_source.OPTIONS_FEED_INDICATIVE)
        self.assertEqual("alpaca:paper", data_source.label())
        self.assertEqual("alpaca:paper", data_source.label(desk_config.VENDOR, desk_config.HOSTS[0]))

    def test_the_free_feed_is_labelled_approximate(self) -> None:
        self.assertEqual("Alpaca paper (IEX stocks, indicative options: approximate)", data_source.WORDS)
        self.assertIn("approximate", data_source.WORDS)
        self.assertNotIn("approximate", data_source.WORDS_OPRA)
        self.assertEqual((data_source.WORDS, data_source.WORDS_OPRA), data_source.ALL_WORDS)

    def test_words_carry_no_digit(self) -> None:
        # the alert lint proves every number of a message was emitted: the source words must hold none
        for words in data_source.ALL_WORDS:
            self.assertIsNone(re.search(r"[0-9]", words), words)
            self.assertTrue(words.isascii())


class WordsTest(unittest.TestCase):
    def test_words_for_feed(self) -> None:
        self.assertEqual(data_source.WORDS, data_source.words_for_feed("indicative"))
        self.assertEqual(data_source.WORDS_OPRA, data_source.words_for_feed("opra"))
        self.assertEqual(data_source.WORDS, data_source.words_for_feed(None))
        self.assertEqual(data_source.WORDS, data_source.words_for_feed("sip"))  # unknown: the conservative label

    def test_words_for_label(self) -> None:
        self.assertEqual(data_source.WORDS, data_source.words_for_label("alpaca:paper"))
        self.assertEqual("Unknown", data_source.words_for_label(None))
        self.assertEqual("Unknown", data_source.words_for_label("unknown"))
        self.assertEqual("Other host", data_source.words_for_label("other:host"))


class LeafModuleTest(unittest.TestCase):
    def test_imports_nothing_from_the_desk(self) -> None:
        path = os.path.join(ROOT, "scripts", "aegis_desk", "data_source.py")
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertIn(node.module, ("__future__", "typing"), ast.dump(node))
                self.assertEqual(0, node.level)
            elif isinstance(node, ast.Import):
                self.fail("unexpected import %s" % ast.dump(node))

    def test_the_lint_accepts_every_source_phrase(self) -> None:
        for words in data_source.ALL_WORDS:
            line = ("leg 1 of 2: closes your short 1 x WMT 2026-11-20 105 call | bid 1.20, ask 1.30, mid 1.25 | "
                    "quote grade B, %s, 15-min delayed, as of 19:31 UTC" % words)
            self.assertTrue(any(pattern.match(line) for pattern in alert_lint.STRUCTURING_LEG_RES), line)


if __name__ == "__main__":
    unittest.main()
