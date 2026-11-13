# The Grok Bot template

`template/grok-bot.json` is the release contract for a public Grok Bot template of the Aegis Desk Lead: the Bot's
profile, the pinned release, the one skill the template carries, and the sha256 of every skill file in the release.
This release does not publish a template. Its status is `unpublished`, and publishing is your action as the owner,
after the tag `v0.2.0` exists.

The README's Start prompt is the main way in, and a template is the second. Grok Bot staff confirmed, through
2026-10-21, that a template import can drop skills. Whichever way the Lead starts, the bootstrap reads back every
skill and compares it with its file in the pinned checkout, whose sha256 this contract records. A skill the import
dropped is installed from the checkout, and one that differs is reported as `mismatch` in the setup receipt.

## The fields and the checks

`python3 scripts/check_grok_template.py` checks the file on every gate run (`bash scripts/check.sh`, step 6):

| Field | In this release | What the check requires |
| --- | --- | --- |
| `status` | `unpublished` | one of `unpublished`, `ready-to-publish`, `published` |
| `name` | `Aegis Desk Lead` | exactly this name |
| `title` | `Head of the options desk` | present |
| `description` | a short description of the desk, with `release 0.1.1` | 1 to 280 characters; contains `never places, modifies or cancels orders`; any `release X.Y.Z` in it equals the `plugin.json` version |
| `source` | this repository, release `0.1.1` | the repository URL of this repository; `release` equals the `plugin.json` version, written without a `v` |
| `skills` | every desk skill, keyed by name | each value is the sha256 of `skills/<name>/SKILL.md` (or an object with that `path` and its `sha256`); the file exists and its digest matches |
| `roleSkills` | the four generated role skills | the same rule, for `skills/aegis-role-<seat>/SKILL.md` |
| `templateSkillNames` | `aegis-desk-bootstrap-and-onboarding` | unique names, each a key of `skills` (a role skill does not count) |
| `publicShareUrl` | absent | only on a `published` template; then an `https` link that also appears in `README.md` and `docs/FAQ.md` |

On this checkout, before the tag exists, it prints:

```text
skip: template/grok-bot.json source.release 0.1.1 has no local tag v0.2.0; tag comparison not run (expected before the release is tagged)
ok: Grok Bot template is unpublished, release 0.1.1, 22 verified skill file(s), exports 1
```

## From unpublished to published

- **`unpublished`** (this release): every hash the file claims must match. It already claims every skill in the tree,
  so the next status is a one-word change.
- **`ready-to-publish`**: the file must also claim every other `skills/aegis-*/SKILL.md` under `skills`, every
  role skill `skills/aegis-role-*/SKILL.md` under `roleSkills`, and export at least one skill. No skill ships
  unhashed.
- **`published`**: it also carries `publicShareUrl`, and that link must appear in `README.md` and `docs/FAQ.md`.
- **Once the tag `v0.2.0` exists** in the checkout, every claimed file must be byte-identical to its copy at the tag
  (`git show v0.2.0:<path>`). Before that, the comparison is skipped out loud, as above.

## The hashes

- Each hash is the sha256 of the whole `SKILL.md` file, frontmatter included. `sha256sum skills/<name>/SKILL.md`
  prints the same value.
- A role skill embeds the `plugin.json` version and the sha256 of its agent file. Editing an agent, or changing the
  version, changes the role skill and so its hash. The order is: the agents final, then
  `python3 scripts/gen_role_skills.py`, then every skill final, then the hashes, last.
- A stale hash fails the gate with a line naming it, for example
  `template/grok-bot.json: skills.aegis-threshold-levels sha256 does not match skills/aegis-threshold-levels/SKILL.md`.

## Authoring and publishing (owner actions, after the tag)

1. Use a fresh Grok Bot. Copy its name, title and description from `template/grok-bot.json`.
2. Add the skill named in `templateSkillNames`, `aegis-desk-bootstrap-and-onboarding`, with its name, description and
   body copied unchanged from the file at the tag. Never a paraphrase, and never a link instead of the body.
3. Add nothing else: no other skill, plugin, memory, routine, secret, conversation or private file.
4. Run `bash scripts/check.sh` and `python3 scripts/check_grok_template.py` on the tagged checkout.
5. Share the Bot publicly and copy its share link. Set `status` to `published`, add the link as `publicShareUrl`,
   and put the same link in `README.md` and `docs/FAQ.md`. The gate refuses a published template otherwise.
6. Keep the publishing Bot; hide it rather than delete it, so the share can be updated for the next release.

How Grok Bot stores a shared skill (for example, whether it keeps the frontmatter's `license` and `metadata`) is
observed when the template is published. The full-file hashes still verify the checkout; the shared copy is compared by
its name, description and body.

## Evaluating a clean install

Add the template as a new Bot and say "Start the desk." The Lead follows the bootstrap skill and
[SETUP.md](../SETUP.md). A passing install returns the receipt of SETUP step 8: a short summary first, then the
evidence: the release and the pin check line, the four Bots, every skill as `template`, `installed` or `pointer` with
no `mismatch`, the Floor, the readbacks, the doctor's verdict, every pending item and the phrases for the next steps.
Its last line is exactly:

```text
Setup stayed read-only: one Alpaca paper key pair was added by you; no order was created or sent.
```

Authoring and publishing mechanics adapted from HyperGrok Trading Desk (Galleon Labs, MIT), release 1.7.0 plus unreleased changes, commit 75a4c79 (docs/GROK_BOT_TEMPLATE.md); see [ATTRIBUTION.md](../ATTRIBUTION.md).
