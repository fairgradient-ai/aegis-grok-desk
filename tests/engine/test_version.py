"""``aegis_desk.version.release_version``: the plugin.json version, or "0.0.0" when unreadable."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import json
import os
import tempfile
import unittest

from aegis_desk import version

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ReleaseVersionTest(unittest.TestCase):
    def test_reads_the_repository_plugin_json(self) -> None:
        with open(os.path.join(ROOT, "plugin.json"), encoding="utf-8") as handle:
            expected = json.load(handle)["version"]
        self.assertEqual(expected, version.release_version())
        self.assertRegex(version.release_version(), r"^\d+\.\d+\.\d+$")
        self.assertEqual(os.path.join(ROOT, "plugin.json"), version.plugin_json_path())
        self.assertEqual(ROOT, version.repository_root())

    def test_explicit_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "plugin.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"name": "x", "version": "3.2.1"}, handle)
            self.assertEqual("3.2.1", version.release_version(path))

    def test_fallbacks(self) -> None:
        self.assertEqual("0.0.0", version.FALLBACK_VERSION)
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "plugin.json")
            self.assertEqual("0.0.0", version.release_version(missing))
            self.assertEqual("0.0.0", version.release_version(tmp))  # a directory
            with open(missing, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            self.assertEqual("0.0.0", version.release_version(missing))
            with open(missing, "w", encoding="utf-8") as handle:
                json.dump({"version": 7}, handle)
            self.assertEqual("0.0.0", version.release_version(missing))
            with open(missing, "w", encoding="utf-8") as handle:
                json.dump({"name": "no version"}, handle)
            self.assertEqual("0.0.0", version.release_version(missing))
            with open(missing, "w", encoding="utf-8") as handle:
                json.dump({"version": "  "}, handle)
            self.assertEqual("0.0.0", version.release_version(missing))
            with open(missing, "w", encoding="utf-8") as handle:
                json.dump(["0.1.0"], handle)
            self.assertEqual("0.0.0", version.release_version(missing))
            with open(missing, "wb") as handle:
                handle.write(b"\xff\xfe")
            self.assertEqual("0.0.0", version.release_version(missing))

    def test_whitespace_is_stripped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "plugin.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"version": " 0.1.0\n"}, handle)
            self.assertEqual("0.1.0", version.release_version(path))


if __name__ == "__main__":
    unittest.main()
