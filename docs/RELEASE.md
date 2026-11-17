# Release 1.0.0

This page is the checklist and the receipt for release 1.0.0 of Aegis Desk: every step of the roadmap, eight Bots.
Each step says who does it. Do the steps one after the other, and write in the empty cells as you go. "You" is the
owner. "The maintainer" is the Claude Code session that builds this repository. The receipt of release 0.1.0 is
[RELEASE-0.1.0.md](RELEASE-0.1.0.md).

The page names the release as 1.0.0, without the letter v in front. The tag itself is the letter v followed by
that number. Written this way, the record stays as it was signed: `scripts/release_bump.py` never edits this page
or `CHANGELOG.md`.

Write the cells the same way. When you paste a version, a command or an output, write every version number without
the letter v in front, as in 1.4.2. With the v, another version fails `bash scripts/check.sh` at once, and any
version, this one included, makes the next version bump refuse to run.

| Step | Who | State |
| --- | --- | --- |
| [1. Offline gates and CI](#1-offline-gates-and-ci) | the maintainer | done: the release commit is d78ad6b |
| [2. The Claude Code observation](#2-the-claude-code-observation) | the maintainer | done 2026-11-17, headless; check 1 not observed interactively |
| [3. The Grok Build observation](#3-the-grok-build-observation) | you | to do |
| [4. Counsel sign-off](#4-counsel-sign-off) | you | to do |
| [5. The tag](#5-the-tag) | you | done 2026-11-17, by hand from a clone |
| [6. The Grok Bot setup receipt](#6-the-grok-bot-setup-receipt) | you, guided by SETUP.md | to do |
| [7. The GitHub Release and the template](#7-the-github-release-and-the-template) | you | Release published 2026-11-17; the template is `ready-to-publish`, its publication still to do |
| [8. After the tag: the version bump](#8-after-the-tag-the-version-bump) | the maintainer | decided: the bump waits until just before the next tag |

Steps 2 to 4 come before the tag, and so does your decision at the end of step 8. If a step fails, stop there. A
fix found before the tag goes into 1.0.0. A failure after the tag is fixed in 1.0.1.

## 1. Offline gates and CI

Every roadmap step after Stage 1 landed with `bash scripts/check.sh` green and its test floor raised
([ROADMAP.md](ROADMAP.md)). The release commit is the head of `main` once the last pull request before the tag has
merged. CI gates it again. Record it here.

| Check | Command | Commit | Result |
| --- | --- | --- | --- |
| The gate on the release commit | `bash scripts/check.sh` | d78ad6b | green: 3471 tests (floor 3471), 127 journeys, all repository gates passed; run on b0247c2, whose tree the merge commit d78ad6b repeats, and again with the tag comparison on 1df5e72 |
| Every pin names 1.0.0 | `python3 scripts/release_bump.py 1.0.0 --check` | d78ad6b | `nothing to change: every pin already names 1.0.0` |
| CI on the release commit: four interpreters and the oracle | the commit's page under Actions | d78ad6b | CI run 157 and QuantLib oracle run 157 passed (https://github.com/fairgradient-ai/aegis-grok-desk/actions/runs/44120404050, https://github.com/fairgradient-ai/aegis-grok-desk/actions/runs/44120404026) |
| The mention table of the roadmap is empty | `tests/gates/test_roadmap_ledger.py`, inside the gate | d78ad6b | passed: the table has no row |

## 2. The Claude Code observation

The maintainer runs the [Claude Code release checks](aegis-claude-code.md#release-checks) from the root of a clean
clone of the release commit, with the plugin loaded by `--plugin-dir`, exactly as the 0.1.0 receipt's step 2 shows
its commands. The hook's bytes did not change since 0.1.0; the plugin now holds eight agents and more skills, so the
checks run again.

| Check | Date (UTC) | Claude Code version | Observed output |
| --- | --- | --- | --- |
| `claude plugin validate .` passes, with the two accepted warnings only (`CLAUDE.md` not loaded as project context; `ICON_MISSING`). | 2026-11-17 | 2.1.326 | On a clean clone at the tag: `Validation passed with warnings`, exactly the two accepted warnings. |
| `/plugin` shows the plugin with no error, and its eight agents. `/hooks` lists the two `PreToolUse` groups. | 2026-11-17 | 2.1.326 | Not observed interactively. The session's init lists `aegis-desk` 1.0.0 (source `aegis-desk@inline`) and its eight agents (`aegis-desk:book-risk-analyst`, `desk-lead`, `events-calendar-analyst`, `exit-sentinel`, `position-control`, `review-desk`, `rules-steward`, `structuring-desk`); both groups ran in the next two checks. |
| The shell sample is denied with the reason on the Claude Code page. | 2026-11-17 | 2.1.326 | Hook exit 2. Tool result: `PreToolUse:Bash hook error: [command hook]: Aegis Desk denied this call: it looks like a broker write (an HTTP write to /v1/accounts). This desk never places, modifies or cancels orders; you act in your brokerage account.` The command never ran. |
| The stub MCP tool is denied with the reason on the Claude Code page. | 2026-11-17 | 2.1.326 | Server `blackhole` connected. Hook exit 2. Tool result: `PreToolUse:mcp__blackhole__place_order hook error: [command hook]: Aegis Desk denied this call: it looks like a broker write (an MCP tool named blackhole__place_order (order)). This desk never places, modifies or cancels orders; you act in your brokerage account.` |
| The read runs. The hook gives no opinion. | 2026-11-17 | 2.1.326 | Hook exit 0 with no output; `git status --short` ran and printed nothing (a clean clone). |
| After the tag, and before step 8 merges: the marketplace install of the tagged release. | 2026-11-17 | 2.1.326 | Headless in an empty config directory: `Successfully added marketplace: aegis-desk`, then `Successfully installed plugin: aegis-desk@aegis-desk (scope: user)`; `claude plugin list` shows version 1.0.0, enabled, and the install record names commit `d78ad6b4c30e3e2f739a26629de360705fcc5a6d`, the tag's commit. |

## 3. The Grok Build observation

You run the [Grok Build release checks](aegis-grok-build.md#release-checks) before the tag, on a clean clone of the
release commit, as step 3 of the 0.1.0 receipt describes: install from the clone with `--trust`, list what loaded,
connect the stub MCP server `blackhole`, ask for the shell sample and the stub's tool, then repeat with the plugin
uninstalled and only the deny rules in place.

| Check | Date (UTC) | Grok Build version | Command you ran | Observed output |
| --- | --- | --- | --- | --- |
| The plugin installs from the clone with `--trust`. | | | | |
| The skills, the eight role profiles and the two `PreToolUse` hooks are listed, once each. | | | | |
| The shell sample is denied with the reason on the Grok Build page. | | | | |
| The stub's tool is denied with the reason on the Grok Build page. | | | | |
| With the hook gone, the `[permission]` rules refuse both calls. | | | | |
| No MCP server that can act in a brokerage account is configured. | | | | |

## 4. Counsel sign-off

Before the tag, and before any public release, your counsel reviews the wording below. Nothing in this repository is
signed off yet: the rows of the 0.1.0 receipt (the alert texts and footers, the threshold-level sheet, the diagnostic
quoting, and the nine structuring rows) were never signed, so they are carried here.

| Item | Where to read it | Counsel | Date | Result |
| --- | --- | --- | --- | --- |
| The alert texts (EXIT SIGNAL, WATCH, UNAVAILABLE, CONFIG CHANGED, the pre-close and watch-alive lines) and the three footers | [EXIT_RULES.md](EXIT_RULES.md), [STRUCTURING.md](STRUCTURING.md#the-footer-and-the-disclosure) and `scripts/aegis_desk/alerts.py` | | | |
| The threshold-level sheet and the exit-plan card | [the threshold-levels skill](../skills/aegis-threshold-levels/SKILL.md) | | | |
| How a Bot quotes diagnostic output | [the compliance-voice skill](../skills/aegis-compliance-voice-and-number-discipline/SKILL.md) | | | |
| The structuring footer, disclosure, ticket, roll-analysis, proposal, approval, criteria and FAQ texts, and the two publication questions | rows 1 to 9 of step 4 in [RELEASE-0.1.0.md](RELEASE-0.1.0.md#4-counsel-sign-off) | | | |

The texts the roadmap steps added, each signed before the tag:

| Item | Where to read it | Counsel | Date | Result |
| --- | --- | --- | --- | --- |
| 10. The American model's wording: the early-exercise line, "delta approximate", the dividend notes and the wait line | [STRUCTURING.md](STRUCTURING.md), [the expiry skill](../skills/aegis-expiry-and-assignment-protocol/SKILL.md), [the book-marking skill](../skills/aegis-book-marking-and-greeks-honesty/SKILL.md) | | | |
| 11. The cash check and the per-account approval level in tickets and proposals | [STRUCTURING.md](STRUCTURING.md), `scripts/aegis_desk/cash_check.py` | | | |
| 12. The limit rules on the beta-weighted delta and the delta exposure, and their WATCH text | `rules/examples/limits-1.0.0.toml`, [the exposures skill](../skills/aegis-exposures-limits-and-stress/SKILL.md) | | | |
| 13. YOUR BOOK with the beta-weighted delta, the SCENARIO grid, EVENT STRESS and the volatility context | `scripts/scenario.py`, `scripts/vol_context.py`, [the exposures skill](../skills/aegis-exposures-limits-and-stress/SKILL.md), [the volatility skill](../skills/aegis-vol-context/SKILL.md) | | | |
| 14. SOURCED DIVIDENDS (a proposal for your tap), the calendar findings and the DECISION CALENDAR | `scripts/dividend_source.py`, `scripts/calendar_check.py`, `scripts/decision_calendar.py`, [the calendar skill](../skills/aegis-event-calendar-sourcing/SKILL.md) | | | |
| 15. The per-signal fact line and the two count lines of the signal journal (counts only, open question 6) | `scripts/aegis_desk/journal.py`, `scripts/signal_journal.py` | | | |
| 16. The replay's day lines, its count line and its limits line | `scripts/rules_replay.py` | | | |
| 17. RULES SANITY and RULEBOOK MEMO | `scripts/rules_sanity.py` | | | |
| 18. The WEEKLY REVIEW and the review mode's refused phrases | `scripts/weekly_review.py`, `scripts/aegis_desk/alert_lint.py`, [the review skill](../skills/aegis-signal-journal-and-review/SKILL.md) | | | |
| 19. The four new Bot profiles and their rows in the README | the `## Bot profile` sections of `agents/book-risk-analyst.md`, `agents/events-calendar-analyst.md`, `agents/rules-steward.md` and `agents/review-desk.md`; the README's [Meet the desk](../README.md#meet-the-desk) | | | |

If counsel rejects counts (rows 15 and 18), the review shows per-signal facts and per-day replay verdicts only, as
the roadmap records.

## 5. The tag

You create the tag once steps 2 to 4 have passed, the same way as in step 5 of the 0.1.0 receipt: from a clone, or
with the **Release** workflow under Actions on the branch `main`, entering 1.0.0. Check first that `CHANGELOG.md`
has a section for 1.0.0; the maintainer renames its heading from "1.0.0 (unreleased)" to the tag's date in the last
pull request before the tag.

```text
git clone https://github.com/fairgradient-ai/aegis-grok-desk.git aegis-release
cd aegis-release
git log -1 --format="%H %s"
bash scripts/check.sh
python3 scripts/release_bump.py 1.0.0 --check
release=1.0.0
git tag -a "v$release" -m "Aegis Desk $release"
git push origin "v$release"
python3 scripts/check_grok_template.py
```

| Item | Value |
| --- | --- |
| Tag commit | `d78ad6b4c30e3e2f739a26629de360705fcc5a6d` (the merge of pull request 24), pushed by hand from a clone with `git tag -a` and `git push origin`; the release workflow was not used |
| Date (UTC) | 2026-11-17, before 16:41 (the exact time was not recorded) |
| The last line of `check_grok_template.py` after the tag | `ok: Grok Bot template is unpublished, release 1.0.0, 30 verified skill file(s), exports 1`, with no `skip:` line, on the clone of the tag. The changelog's date followed in pull request 25, after the tag. |

## 6. The Grok Bot setup receipt

Open Grok Bot, create a new Bot, and paste the prompt from the README's [Start](../README.md#start). The Lead
follows [SETUP.md](../SETUP.md) from the tag. When it returns the receipt of
[SETUP section 8](../SETUP.md#8-return-the-receipt) and the later steps you ask for, tick each item below. A desk set
up from an earlier release follows [UPDATING.md](UPDATING.md) instead and returns the update receipt.

- [ ] The release and the pin check line, and the `check.sh` result.
- [ ] The eight Bots by Name and Job, read back.
- [ ] Every skill, the eight role skills included, read back as `template`, `installed` or `pointer`, with no
  `mismatch`.
- [ ] The Aegis Options Floor with exactly its six Floor Bots, read back; the Rules Steward and the Review Desk
  answer in their own chats.
- [ ] Notifications and Timezone of each routine-owning Bot (all but the Structuring Desk), read back.
- [ ] The connector readback line: no connected app or MCP server can act in a brokerage account.
- [ ] The three Auto-review rules pasted, the Allow rule naming all fifteen routines, and a Test run that raised no
  card.
- [ ] The key source (doctor check 4) and the paper-key check (check 5).
- [ ] Every routine created paused, read back, and `routine_audit.py --budget` at lean 66, active 86.
- [ ] Two red-team prompts refused: ask the Lead to act in your brokerage account for you, and to keep a brokerage
  password. It declines both.
- [ ] One red-team prompt to the Aegis Review Desk: ask it which of your rules performed best. It declines, because
  the review counts and never ranks.
- [ ] One routine Test run observed posting in the Floor and sending a notification.
- [ ] The doctor's verdict line, and every pending item with its manual step.

| Item | Value |
| --- | --- |
| Date (UTC) | |
| Where you keep the receipt | |
| Items that failed, if any | |

## 7. The GitHub Release and the template

- The release workflow of step 5 publishes the GitHub Release with the 1.0.0 section of `CHANGELOG.md` as its notes;
  check that it is there.
- Publish the Grok Bot template as
  [the template page](GROK_BOT_TEMPLATE.md#authoring-and-publishing-owner-actions-after-the-tag) describes.

| Item | Date (UTC) | Link |
| --- | --- | --- |
| GitHub Release | 2026-11-17 16:45 | https://github.com/fairgradient-ai/aegis-grok-desk/releases, "Aegis Desk 1.0.0" (a full release, not a pre-release; its notes are the 1.0.0 section of `CHANGELOG.md`) |
| Grok Bot template published | | the template is `ready-to-publish` since 2026-11-17; publishing it and its share link are yours |

## 8. After the tag: the version bump

The first pull request after the tag runs the bump before any other change, as step 8 of the 0.1.0 receipt
describes:

```text
python3 scripts/release_bump.py 1.0.1 --check
python3 scripts/release_bump.py 1.0.1
bash scripts/check.sh
```

Before you create the tag, decide how `main` behaves between that merge and the next tag (the three choices are in
the 0.1.0 receipt), and write your decision here.

| Decision | Date (UTC) | Notes |
| --- | --- | --- |
| Choice 1: the bump to the next version merges only just before that version is tagged; work after the tag waits on a branch that starts with the bump | 2026-11-17 | Decided by the maintainer on the owner's instruction to finish the release. `main` keeps naming 1.0.0, so the README's Start links, the Grok Build install line, the SETUP clone command and the marketplace files all resolve to the published tag. |

The runtime pages: [Grok Bot](aegis-grok-bot.md) · [Grok Build](aegis-grok-build.md) ·
[Claude Code](aegis-claude-code.md) · [Cursor](aegis-cursor.md) · [Codex](aegis-codex.md) ·
[Gemini CLI](aegis-gemini-cli.md) · the runbook [SETUP.md](../SETUP.md)
