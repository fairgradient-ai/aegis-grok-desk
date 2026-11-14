"""``docs/ROADMAP.md``'s ledger ("Where each item is mentioned today") against the tree.

The ledger has two parts. The item table maps each planned item to the files that mention it. The mention table
("Mentions by file") lists every file that names a later stage, with how many times its whitespace-collapsed text names
Stage 2 and how many times Stage 3 (the stage word followed by the number, spaced, hyphenated or joined, in any case,
even across a line break). This gate checks that the mention table is exactly the tree: every Markdown, Python, TOML,
JSON, YAML, text and shell file outside hidden directories other than ``.github`` that names a later stage is listed
with its two counts, and no other file is listed. The roadmap, the changelog and this file are exempt: they record the
plan, its history and the pattern's fixtures. It also checks that every file of the mention table appears in at least
one item row, and that every path the ledger lists exists.

So a pull request that adds, moves or removes a later-stage mention anywhere must update the counts in the same pull
request, and the reviewer sees which item row the file sits in. The gate cannot tell which item a sentence is about;
that stays a review question. Because the counts shrink to nothing as steps land, the gate passes at every step and at
the end: a stage is complete when no row of the mention table has a count in its column.
"""
from __future__ import annotations

import os
import re
import unittest
from typing import Dict, Iterator, Set, Tuple

# isort: off
import tests._path  # noqa: F401
from tests.gates._trees import ROOT
# isort: on

ROADMAP = os.path.join("docs", "ROADMAP.md")
NOT_LISTED = ("CHANGELOG.md", "docs/ROADMAP.md", "tests/gates/test_roadmap_ledger.py")
EXTENSIONS = (".md", ".py", ".toml", ".json", ".yml", ".yaml", ".txt", ".sh")
STAGE_TWO = re.compile(r"\bstage[- ]?2\b", re.IGNORECASE)
STAGE_THREE = re.compile(r"\bstage[- ]?3\b", re.IGNORECASE)
LEDGER_HEADING = "## Where each item is mentioned today"
MENTIONS_HEADING = "### Mentions by file"
LEDGER_PATH = re.compile(r"`([A-Za-z0-9_./-]+\.(?:md|py|toml|json|yml|yaml|txt|sh))`")
MENTION_ROW = re.compile(r"^\|\s*`([A-Za-z0-9_./-]+)`\s*\|\s*([0-9]+)\s*\|\s*([0-9]+)\s*\|\s*$", re.MULTILINE)


def files() -> Iterator[str]:
    for folder, dirs, names in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d == ".github" or not (d.startswith(".") or d == "__pycache__"))
        for name in sorted(names):
            if name.endswith(EXTENSIONS):
                yield os.path.relpath(os.path.join(folder, name), ROOT).replace(os.sep, "/")


def counts(text: str) -> Tuple[int, int]:
    """How many times the whitespace-collapsed ``text`` names Stage 2 and Stage 3."""
    collapsed = " ".join(text.split())
    return len(STAGE_TWO.findall(collapsed)), len(STAGE_THREE.findall(collapsed))


def ledger_parts(roadmap: str) -> Tuple[Set[str], Dict[str, Tuple[int, int]]]:
    """The paths of the item table, and the mention table as ``{path: (stage 2 count, stage 3 count)}``."""
    start = roadmap.index(LEDGER_HEADING)
    following = roadmap.find("\n## ", start + len(LEDGER_HEADING))
    section = roadmap[start:] if following < 0 else roadmap[start:following]
    split = section.index(MENTIONS_HEADING)
    items = set(LEDGER_PATH.findall(section[:split]))
    mentions = {path: (int(two), int(three)) for path, two, three in MENTION_ROW.findall(section[split:])}
    return items, mentions


class RoadmapLedgerTest(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        with open(os.path.join(ROOT, ROADMAP), encoding="utf-8") as handle:
            cls.roadmap = handle.read()
        cls.items, cls.mentions = ledger_parts(cls.roadmap)

    def test_the_mention_table_is_the_tree(self) -> None:
        found: Dict[str, Tuple[int, int]] = {}
        scanned = 0
        for path in files():
            scanned += 1
            if path in NOT_LISTED:
                continue
            with open(os.path.join(ROOT, path), encoding="utf-8", errors="replace") as handle:
                two, three = counts(handle.read())
            if two or three:
                found[path] = (two, three)
        self.assertGreater(scanned, 300)
        self.assertEqual(found, self.mentions)

    def test_the_scan_sees_the_roadmap(self) -> None:
        # The roadmap always names both stages, at every step and after the last, so this proves the pattern reads
        # real text even when the mention table has emptied.
        two, three = counts(self.roadmap)
        self.assertGreater(two, 0)
        self.assertGreater(three, 0)

    def test_every_file_in_the_mention_table_is_in_an_item_row(self) -> None:
        self.assertEqual([], sorted(path for path in self.mentions if path not in self.items))

    def test_every_ledger_path_exists(self) -> None:
        listed = self.items | set(self.mentions)
        self.assertEqual([], sorted(path for path in listed if not os.path.isfile(os.path.join(ROOT, path))))

    def test_the_pattern_counts_each_spelling(self) -> None:
        for text, expected in (("planned for Stage 2 (not in this release)", (1, 0)),
                               ("the Stage-2 seats", (1, 0)),
                               ("a STAGE3 feature", (0, 1)),
                               ("planned for Stage\n  3 (not in this release)", (0, 1)),
                               ("A Stage 2 or Stage 3 feature; planned for Stage 2.", (2, 1)),
                               ("In Stage 1 they are European", (0, 0)),
                               ("stage 20", (0, 0)),
                               ("backstage 2", (0, 0)),
                               ("step 2.1", (0, 0))):
            with self.subTest(text=text):
                self.assertEqual(expected, counts(text))

    def test_a_second_promise_in_a_listed_file_changes_its_count(self) -> None:
        before = "Charter. Weekly reviews are planned for Stage 3 (not in this release)."
        after = before + " Weekly expirations are planned for Stage 2 (not in this release)."
        self.assertNotEqual(counts(before), counts(after))

    def test_the_ledger_is_read_in_two_parts(self) -> None:
        sample = ("# T\n\n## Where each item is mentioned today\n\n| a | 2.1 | `docs/A.md`, `scripts/b.py` |\n\n"
                  "### Mentions by file\n\n| File | 2 | 3 |\n| --- | --- | --- |\n| `docs/A.md` | 2 | 0 |\n\n"
                  "## Next\n| `c.md` | 1 | 1 |\n")
        self.assertEqual(({"docs/A.md", "scripts/b.py"}, {"docs/A.md": (2, 0)}), ledger_parts(sample))


if __name__ == "__main__":
    unittest.main()
