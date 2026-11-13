"""The README pictures under ``assets/``: in step with their generator, readable, and visible to the link gate.

``assets/src/build_graphics.py --check`` renders every SVG again (the demo card from ``opening_bell.py --demo``,
which writes nothing) and must find the committed files identical. Every text colour class in each SVG's style
block must reach the WCAG AA contrast of 4.5:1 against every dark surface the cards are drawn on. Every SVG that
README.md shows, through HTML ``<picture>``/``<img>`` (which the link gate does not check) or markdown, must exist
and be named by a markdown link or image in a file the link gate scans, so a rename breaks the gate, not the page.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from typing import Dict, List, Set

# isort: off
import tests._path  # noqa: F401
from tests.gates._trees import ROOT
# isort: on

ASSETS = os.path.join(ROOT, "assets")
GENERATOR = os.path.join(ASSETS, "src", "build_graphics.py")
README = os.path.join(ROOT, "README.md")
ASSETS_README = os.path.join(ASSETS, "src", "README.md")
# The card backgrounds (a gradient from the first to the second) and the panels drawn on them.
SURFACES = ("#0c1322", "#121c30", "#17233a", "#1d2b46")
TEXT_CLASSES = ("tx", "mu", "fa", "az", "am", "co")
AA_NORMAL_TEXT = 4.5


def luminance(colour: str) -> float:
    value = colour.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    a, b = luminance(first), luminance(second)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def svg_files() -> List[str]:
    return sorted(name for name in os.listdir(ASSETS) if name.endswith(".svg"))


def class_fills(path: str) -> Dict[str, str]:
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    return dict(re.findall(r"\.([a-z]+)\{fill:(#[0-9a-fA-F]{6})\}", text))


def markdown_targets(path: str) -> Set[str]:
    """Each ``](target)`` of a markdown file, resolved to a path relative to the repository root."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    base = os.path.dirname(path)
    found: Set[str] = set()
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        target = target.split("#", 1)[0]
        if not target or "://" in target:
            continue
        found.add(os.path.relpath(os.path.normpath(os.path.join(base, target)), ROOT).replace(os.sep, "/"))
    return found


class GeneratorInStepTests(unittest.TestCase):
    def test_committed_svgs_match_a_fresh_render(self) -> None:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("APCA_API_KEY_ID", None)
        env.pop("APCA_API_SECRET_KEY", None)
        proc = subprocess.run([sys.executable, GENERATOR, "--check"], cwd=ROOT, env=env, capture_output=True,
                              text=True, encoding="utf-8", timeout=120, check=False)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for name in ("hero.svg", "hero-mobile.svg", "desk-flow.svg", "desk-flow-mobile.svg", "demo-card.svg"):
            self.assertIn("ok assets/%s " % name, proc.stdout)


class ContrastTests(unittest.TestCase):
    def test_every_text_colour_reaches_aa_on_every_surface(self) -> None:
        names = svg_files()
        self.assertTrue(names)
        for name in names:
            fills = class_fills(os.path.join(ASSETS, name))
            for cls in TEXT_CLASSES:
                self.assertIn(cls, fills, "%s: no .%s fill" % (name, cls))
                for surface in SURFACES:
                    ratio = contrast(fills[cls], surface)
                    self.assertGreaterEqual(ratio, AA_NORMAL_TEXT, "%s: .%s %s on %s is %.2f:1"
                                            % (name, cls, fills[cls], surface, ratio))


class LinkGateSeesThePicturesTests(unittest.TestCase):
    def test_every_readme_svg_exists_and_has_a_markdown_link(self) -> None:
        with open(README, encoding="utf-8") as handle:
            readme = handle.read()
        shown = set(re.findall(r"(assets/[A-Za-z0-9_.-]+\.svg)", readme))
        self.assertGreaterEqual(len(shown), 5)
        linked = markdown_targets(README) | markdown_targets(ASSETS_README)
        for path in sorted(shown):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, path)), "%s is shown in README.md but missing" % path)
            self.assertIn(path, linked, "%s has no markdown link or image the link gate can check" % path)

    def test_every_committed_svg_has_a_markdown_link(self) -> None:
        linked = markdown_targets(README) | markdown_targets(ASSETS_README)
        for name in svg_files():
            self.assertIn("assets/" + name, linked)


if __name__ == "__main__":
    unittest.main()
