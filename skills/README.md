# Skills

The Stage-1 desk's skills, in the portable `SKILL.md` format. Every skill sits in its own folder `skills/aegis-<name>/`
with three files:

- `SKILL.md`: frontmatter (`name` equal to the folder, a `description`, `license: MIT` and `metadata` with `version`,
  `author` and `category`), then a body of at most 320 lines;
- `LICENSE`: the MIT licence of this repository;
- `ATTRIBUTION.md`: an `origin: new` or `origin: adapted` line. The one adapted skill,
  `aegis-desk-bootstrap-and-onboarding`, also carries its credit line in its body, so the credit travels with the
  skill.

`bash scripts/check.sh` checks all of this, and checks that every skill is linked here and named in
[SETUP.md](../SETUP.md). Each Bot loads the skills its agent file lists under `skills:`; SETUP step 5 installs them with
a readback each. `template/grok-bot.json` records the sha256 of every `SKILL.md`.

| Skill | What it teaches | Loaded by |
| --- | --- | --- |
| [aegis-desk-charter](aegis-desk-charter/SKILL.md) | the desk's constitution: the seats, one file and one writer, the verdict-first message grammar and its three footers, UNAVAILABLE as a verdict, the exit-code table, the Floor protocol and which chat carries which message; load it first | every Stage-1 Bot |
| [aegis-compliance-voice-and-number-discipline](aegis-compliance-voice-and-number-discipline/SKILL.md) | only numbers a tool printed; the alert lint, its codes and its structuring mode; your-rule-fired wording; no order fields anywhere, and candidates only from the Structuring Desk on request; the AI-role disclosure; the one reply when you ask a Bot to trade | every Stage-1 Bot |
| [aegis-routines-and-quiet-rules](aegis-routines-and-quiet-rules/SKILL.md) | how routines run on Grok Bot: the manifest, one `routine.py` command per run posted verbatim, the quiet rules, heartbeats, the re-post ladder, the lean and active profiles, Timezone and the dead-man rule | every Stage-1 Bot |
| [aegis-market-data-budget-and-quality](aegis-market-data-budget-and-quality/SKILL.md) | the GET-only sandbox client, the token, the quote cache and its per-minute ledger, the 15-minute delay, quote grades A to F, mid and liquidation marks, and why a stale quote is UNAVAILABLE | every Stage-1 Bot |
| [aegis-desk-incident-playbooks](aegis-desk-incident-playbooks/SKILL.md) | what each failure looks like in the desk's own words (INCIDENT, WITHHELD, UNAVAILABLE, CONFIG CHANGED) and the read-only response, ending with your check-your-broker list | every Stage-1 Bot |
| [aegis-desk-bootstrap-and-onboarding](aegis-desk-bootstrap-and-onboarding/SKILL.md) | the first run on Grok Bot, from the pinned release to the receipt; the later steps start with the owner's phrases (SETUP step 9) (adapted) | Aegis Desk Lead |
| [aegis-rules-dsl-and-change-control](aegis-rules-dsl-and-change-control/SKILL.md) | the rulebook as data: grammar, bounds, metrics, origin marks, the adoption lint, the candidate-file interview, adoption outside regular hours, shas, carry-forward and rollback | Aegis Desk Lead, Aegis Exit Sentinel |
| [aegis-exit-watch-and-signal-protocol](aegis-exit-watch-and-signal-protocol/SKILL.md) | how a book check decides and posts: three-valued rules, the verdict precedence, transitions only, confirmations, re-arm and cooldown, acknowledgements, the re-post ladder and the pre-close line | Aegis Exit Sentinel |
| [aegis-threshold-levels](aegis-threshold-levels/SKILL.md) | reading the threshold-level sheet and the plan card: each rule as a price, band or date with its arithmetic, tick rounding, resting-eligible lines and alert levels | Aegis Exit Sentinel |
| [aegis-book-marking-and-greeks-honesty](aegis-book-marking-and-greeks-honesty/SKILL.md) | the two marks, time to expiry and spot, implied volatility and when it is refused, European Greeks in desk units, P/L from the desk's own snapshot, and structure metrics | Aegis Exit Sentinel, Aegis Structuring Desk |
| [aegis-covered-call-and-cash-secured-put-management](aegis-covered-call-and-cash-secured-put-management/SKILL.md) | how the desk measures covered calls and cash-secured puts, which example rules reach them, the ex-dividend test and its bound, and the row tags | Aegis Exit Sentinel, Aegis Structuring Desk |
| [aegis-protective-put-and-collar-management](aegis-protective-put-and-collar-management/SKILL.md) | how the desk measures hedges: the continuity rules, the put's delta, floor and cover, automatic exercise and the collar's call | Aegis Exit Sentinel, Aegis Structuring Desk |
| [aegis-vertical-spread-management](aegis-vertical-spread-management/SKILL.md) | vertical spreads on their net credit or debit, the cost to close from both legs, the between-the-strikes rule and the short leg's alerts | Aegis Exit Sentinel |
| [aegis-positions-csv-book-of-record](aegis-positions-csv-book-of-record/SKILL.md) | your `positions.csv`: who writes it, the columns and money conventions, errors and quarantine, and the exact lines for each event, which you apply | Aegis Position Control |
| [aegis-expiry-and-assignment-protocol](aegis-expiry-and-assignment-protocol/SKILL.md) | the expiry week: the one-cent rule, the act-by time (a short option's session close, a long option's broker cutoff), settlement styles, the pin and dividend tiers, early closes and the inferred outcomes you confirm with your broker | Aegis Position Control |
| [aegis-overlay-proposals](aegis-overlay-proposals/SKILL.md) | overlays on shares you hold (a covered call, a protective put, a collar), your `structuring.toml` with every key, bound and default, the disclosure and the interview, the objectives in words, the best-candidate framing and how to read a proposal | Aegis Structuring Desk |
| [aegis-roll-decision-framework](aegis-roll-decision-framework/SKILL.md) | a roll as a close plus an open, the replacement set, the score and its tie-breaks, the FAILS_CRITERION labels and how your tags and criteria accept them, and why no candidate passing is a complete answer | Aegis Structuring Desk |
| [aegis-ticket-and-unwind-order](aegis-ticket-and-unwind-order/SKILL.md) | the ticket line by line: the fixed leg grammar, the canonical leg order, the approval-level convention, the cash and realized lines, the 30-minute validity, exercise by exception and what follows a fill; you supply the limit | Aegis Structuring Desk |

The skills describe your adopted rules, your criteria and the tools' output. None of them chooses a rule, a threshold
or a criterion for you.

## Later stages

- `aegis-exposures-limits-and-stress`, exposures, limits and stress views, is planned for Stage 2 (not in this release).
- `aegis-vol-context`, volatility context that never changes a verdict, is planned for Stage 2 (not in this release).
- `aegis-event-calendar-sourcing`, sourced earnings and ex-dividend dates, is planned for Stage 2 (not in this release).
- `aegis-signal-journal-and-review`, the signal journal and the weekly review, is planned for Stage 3 (not in this release).

## Role skills

Role skills are generated from each agent's `## System prompt` section by `scripts/gen_role_skills.py`: edit the
agent, never a role skill, and run the generator (`check.sh` step 10 fails on drift). Each of the four Stage-1 Bots
gets its own role skill as its standing instructions.

<!-- role-skills:start -->
| Role skill | Generated from | Seat |
| --- | --- | --- |
| [aegis-role-desk-lead](aegis-role-desk-lead/SKILL.md) | `agents/desk-lead.md` | floor |
| [aegis-role-exit-sentinel](aegis-role-exit-sentinel/SKILL.md) | `agents/exit-sentinel.md` | floor |
| [aegis-role-position-control](aegis-role-position-control/SKILL.md) | `agents/position-control.md` | floor |
| [aegis-role-structuring-desk](aegis-role-structuring-desk/SKILL.md) | `agents/structuring-desk.md` | floor |
<!-- role-skills:end -->
