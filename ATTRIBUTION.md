# Reuse and attribution

Aegis Desk is created by FairGradient and released under the MIT license ([LICENSE](LICENSE)).

## Adapted material

Some repository mechanics are adapted from
[HyperGrok Trading Desk](https://github.com/galleonlabs/hypergrok-trading-desk) by
[Andrew Wilkinson](https://andrewwilkinson.io) and [Galleon Labs](https://github.com/galleonlabs), release 1.7.0 plus
unreleased changes, commit 75a4c79 (`75a4c79118acb0e26d9bad818eca18ef1df8f3cc`), used under the MIT license. Its
licence is reproduced verbatim in [LICENSES/hypergrok-trading-desk.txt](LICENSES/hypergrok-trading-desk.txt)
("Copyright (c) 2026 Galleon Labs").

Only mechanics were adapted: how the gates check a repository of this shape, how the manifests and the template are
laid out, and how a Grok Bot bootstrap is staged and verified. No role, skill or alert text was copied.

| This repository | Adapted from (upstream path) | What was adapted |
| --- | --- | --- |
| `scripts/check.sh` | `scripts/check.sh` | the structure of the repository gate; the checks are Aegis Desk's own |
| `scripts/check_manifests.py` | `scripts/check_manifests.py` | the manifest agreement checks |
| `scripts/check_grok_template.py` | `scripts/check_grok_template.py` | the template contract check |
| `scripts/cli_journeys.py` | `scripts/cli_journeys.py` | the command-journey harness |
| `scripts/aegis_desk/contract.py` | `scripts/cli_contract.py` | the machine-readable argument errors and the `--schema` self-description |
| `.github/workflows/ci.yml` | `.github/workflows/ci.yml` | the CI workflow shape |
| `hooks/broker_write_gate.py` | `hooks/exchange_write_gate.py` | the PreToolUse decision object and the quiet handling of reads and malformed input |
| `hooks/hooks.json` | `hooks/exchange-write-gate.json` | the Claude Code hook registration format |
| `tests/gates/test_broker_write_gate.py` | `scripts/test_exchange_write_gate.py` | the process-contract and repository-tools tests |
| `plugin.json`, `.grok-plugin/plugin.json`, `.grok-plugin/marketplace.json`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.cursor-plugin/plugin.json`, `.agents/plugins/marketplace.json`, `gemini-extension.json` | the same paths | the manifest layout for each runtime |
| `template/grok-bot.json` | `template/grok-bot.json` | the Grok Bot template contract |
| `docs/GROK_BOT_TEMPLATE.md` | `docs/GROK_BOT_TEMPLATE.md` | the template authoring and publishing mechanics |
| `SETUP.md` | `SETUP.md` | the runbook structure |
| `skills/aegis-desk-bootstrap-and-onboarding/SKILL.md` | `skills/hypergrok-bootstrap/SKILL.md` | the staged bootstrap mechanics: verifying the pinned release, a readback per skill, the group chat, the evidence receipt |

The adapted code files, `SETUP.md` and `docs/GROK_BOT_TEMPLATE.md` carry a credit line that names the release, the
commit and the upstream path. JSON files cannot hold one, so this table names them. The bootstrap skill's folder has
its own `ATTRIBUTION.md` with `origin: adapted`, and the skill's body carries the line "Adapted from HyperGrok Trading
Desk (Galleon Labs, MIT), commit 75a4c79; see ATTRIBUTION.md.", so the credit travels with the skill.

## Original material

Everything else is original to Aegis Desk (FairGradient, MIT): the roles under `agents/`, the other skills and the
generated role skills, the routines and the Auto-review texts, the desk rule `rules/aegis-desk.mdc`, the example
rulebook, `hooks/cursor-hooks.json`, the data contracts, the tools and the engine under `scripts/`, the tests and the
fixtures. The vendored TOML reader is original too and carries its own licence file
([scripts/aegis_desk/_vendor/LICENSE](scripts/aegis_desk/_vendor/LICENSE)). The pricing math was ported from the
FairGradient Aegis quant engine; its sources are in [docs/PROVENANCE.md](docs/PROVENANCE.md).

Every skill folder carries its own `LICENSE` and an `ATTRIBUTION.md` with an `origin: new` or `origin: adapted` line.

## Give visible credit

If you reuse this repository, keep the licence notices and link to the source.
