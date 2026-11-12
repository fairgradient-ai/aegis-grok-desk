---
name: aegis-compliance-voice-and-number-discipline
description: The voice and number rules every Aegis desk Bot follows in every post. Only numbers a tool printed, never arithmetic; the alert lint, its finding codes, its structuring mode and its red-team fixtures; the owner's-rule-fired wording with no advice; no order fields anywhere, and candidates in score order only from the Structuring Desk on request; accurate AI-role disclosure with the three fixed footers; and the one reply when the owner asks a Bot to trade.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: compliance
---

# Compliance voice and number discipline

The desk informs; the owner decides. Every rule is the owner's, adopted one by one. An alert says that the owner's
own rule fired, with the numbers that made it fire and the plan the owner wrote down. It carries no order fields, no
strike or expiry to move to and no ranked alternatives, and the owner acts, or not, in the brokerage account.

Why it is built this way: FINRA Notice to Members 01-23 discusses when an online communication becomes a call to
action tailored to one investor. The alerts are designed to stay on the informational side of that line. This is a
description of the design, not legal advice; the owner's own counsel answers that question. The reasoning is set
out in [rules/examples/README.md](../../rules/examples/README.md).

The Aegis Structuring Desk is different. On the owner's request, its tools tailor candidates to the owner's own
holdings, score them on the owner's objective and name one best candidate. That is a personalized suggestion, and
the desk says so: its text never claims to be anything else, never carries the rules footer's words about advice,
and never describes the approval-level check as a judgment of whether a trade fits the owner. Its counsel review is
pending before any public release.

## Numbers: only what a tool printed

- Never add, subtract, multiply, divide, round, convert, average or restate a number. That holds even for two numbers
  one tool printed side by side. No "about", no "roughly", no totals of your own.
- Quote the whole line the tool printed. If the owner wants a number no tool prints, say that no tool prints it in
  this release.
- Dates, times and identifiers too: only as printed. Never move a time between UTC and ET yourself.
- Every number in a desk message already carries its source, delay and UTC time. Keep them together; never quote a
  price without its quote segment.
- No claim about returns, outcomes or performance, and no forecast of any kind. A structuring score ranks candidates
  on the owner's objective only; its fixed caveat says it is not a forecast.

## Before anything is posted

- Routine output is posted verbatim, the single word `quiet` included. `routine.py` lints the whole message before it
  prints anything. A part that fails its own lint is replaced by a fixed WITHHELD line naming the finding codes,
  never the text; a whole message that fails is replaced by an INCIDENT line.
- Seven tools lint their own text, so their full output may be posted when the owner asks: the `book_check.py` dry
  run, `trigger_levels.py` (the sheet or a plan card), `eod_summary.py` and `opening_bell.py`, and the Structuring
  Desk's `exit_ticket.py`, `propose.py` and `roll_analysis.py`, which lint in structuring mode. Those three print
  only the WITHHELD line and the footer when their text fails.
- Every other tool's output is diagnostic: `positions_check.py`, `csv_recon.py` (with or without `--diff`),
  `expiry_checklist.py`, `assignment_watch.py`, `desk_doctor.py`, `routine_audit.py`, `ack.py list`, `quote_qc.py`,
  `market_clock.py`. Quote it only when the owner asks, unchanged, in a fenced block headed by the exact command.
  Never paraphrase it and never add a number to it. The proposed rows of `csv_recon.py --diff` appear only in Aegis
  Position Control's chat.
- Never post `book.py` output or `reports/book.md` as a message: neither is linted, and the book's row form fails the
  lint's order-field rule.
- Your own sentences carry no number, no verdict and none of the words below. No tool lints a Bot's free chat text,
  so this discipline is yours.

## The alert lint

```text
python3 /workspace/aegis-grok-desk/scripts/alert_lint.py --message-file PATH --emitted-file PATH --desk-root /workspace/aegis-desk
```

- It proves that a message carries only numbers the desk emitted, the fixed footer and no trade language. Its exit
  code is 0 when the message passes and 1 when it fails; a failed message must not be posted. `--footer rules`,
  `snapshot` or `structuring` names the footer the last line must be; the default is the emitted document's footer
  key, else rules.
- The finding codes: `empty_message`, `non_ascii`, `number_not_emitted`, `identifier_not_emitted`,
  `forbidden_language`, `order_field`, `imperative_start`, `footer_missing`.
- Numbers: every numeric token, whatever it is attached to (a letter, a hyphen, a percent sign), must equal a string
  the tool emitted. Trade ids, signal ids, rulebook versions, shas, clock times, dates and rule ids are identifiers.
- The alphabet: printable ASCII, plus the middle dot inside the footer line only. A look-alike character, an
  invisible space or a typographic minus fails.
- A problem line never repeats the forbidden text it reports.

The red-team fixtures in `tests/fixtures/alerts/` fail first on one rule each: `fail_2x_credit`, `fail_21_dte` and
`fail_pct_52_4` on `number_not_emitted`; `fail_you_should` and `fail_order_kind` on `forbidden_language`;
`fail_order_field` on `order_field`; `fail_imperative_execute` and `fail_imperative_fill` on `imperative_start`. Two
runs from the repository root on the synthetic fixtures:

```text
$ python3 scripts/alert_lint.py --message-file tests/fixtures/alerts/fail_you_should.txt --emitted-file tests/fixtures/alerts/fail_you_should.emitted.json
alert lint: 1 problem; the message must not be posted
line 1, column 289: forbidden language (advice, FL01)
$ python3 scripts/alert_lint.py --message-file tests/fixtures/alerts/pass_exit_signal.txt --emitted-file tests/fixtures/alerts/pass_exit_signal.emitted.json
ok: the message passes the alert lint (9 numbers checked)
```

## Words that never appear

The exact list lives in one place, `FORBIDDEN_LANGUAGE` in `scripts/aegis_desk/alert_lint.py`. This skill names the
categories without spelling them out, so that it never prints an instruction itself. Matching ignores case and
treats spaces, hyphens and underscores between the words of a phrase alike.

| Codes | Category | What it catches |
| --- | --- | --- |
| FL01 to FL04 | advice | "you" with the modal of obligation within two words of it; the verb that means to advise, and its noun, in any form |
| FL05 to FL08 | trade verbs | the two verbs for trading into or out of a position, in every tense |
| FL09, FL10 | order instructions | the phrase for putting an order in, and the verb for sending one |
| FL11 to FL17, FL25 | order kinds and durations | an order named by its price condition, its duration or its stop; the open-ended duration forms; a resting order |
| FL18, FL19 | destinations of a roll | the destination of a roll: the verb followed within three words by a preposition of destination |
| FL20 | price objectives | the word for a price objective |
| FL21 to FL24 | order-ticket fields | a side-and-intent token joined with underscores; a field name followed by a colon or an equals sign; a size joined to a price by an at sign; a limit followed by a price |

The structuring mode (`--footer structuring`, only the Structuring Desk's tickets, its roll analyses and its proposals)
keeps every row above and adds `STRUCTURING_FORBIDDEN_LANGUAGE`, again named here only by category:

| Codes | Category | What it catches |
| --- | --- | --- |
| SL01 to SL07, SL11, SL17 | advice | advice from any subject and in any modal form, a first-person view included; a candidate singled out or preferred, by anyone, the desk and its tools included; praise of a choice; a statement that a trade fits the owner or matches their situation |
| SL08, SL09, SL13, SL15, SL16, SL18, SL22 | claims | words that rate how probable an outcome is, in any form; assurances against loss, and claims that a choice carries no risk; claims of what a choice earns; a claim that the text is not advice; words of certainty; words that praise a price as good value, or a stated confidence |
| SL10 | urgency | a call to act at once, in any wording |
| SL12, SL21 | order instruction | the bare verb for putting an order in, and a trade verb outside the base table, in any form |
| SL14, SL20 | order field | a price with a decimal point after the words for a price condition, or a few words after a limit, a payment or a price |
| SL19 | order kind | the abbreviations of order types, durations and routes, a one-session duration in capitals or in words, and the verb for routing |

It also refuses a line with a leg reference anywhere (`leg 2 of 2`, `leg 2/2`, the numbers in words, any case or
joining) or a leg's effect words, unless the line is exactly the fixed leg grammar with only the quote segments the
tools print; rule content (a rule, a plan word, a verdict word, a rulebook version) under the structuring footer, the
plan word included (`your plan: close`); and the sentence starts IS10 to IS42: the imperatives of choosing, opening,
keeping, hedging and adding, and the trade verbs.

## Sentence starts the lint refuses

- A sentence never starts with the word close or roll or place or enter or exit or execute or fill, or with a trade
  verb (IS01 to IS09).
- For the lint, a sentence starts at the start of a line and after an exclamation mark, a question mark, a
  semicolon, a colon, a pipe, a comma, a full stop followed by a space, and a dash between spaces. Quotes, brackets,
  list marks and the filler words (so, then, and, now, please, just, simply, also, kindly) are skipped first.
- Two fixed phrases are the exceptions: the plan word standing alone in your plan: close (followed by a pipe, a
  bracket, a semicolon or the end of the line), and the alert kind EXIT SIGNAL in capitals.
- The words fill and execute are allowed in the middle of a sentence. The threshold sheet's fixed warning
  `a limit at this price executes immediately` passes.
- Describe a level's rounding as closing a short leg or closing a long leg, never by trade-side words.

## The owner's rule fired

- Every alert cites the owner's own rule, its rulebook version and its sha, the reading and the owner's threshold.
  A synthetic example (the fixture desk at the 15:35 slot of 2026-11-20, run at 19:50 UTC):

  ```text
  your rule TP80 (rules 1.0.0, sha cc09859c) fired: credit captured 95.32 % (your threshold: at or above 80 %)
  ```

- The plan words come from the owner's rulebook (the plan close, the plan roll_review or the plan hold_review,
  printed as close, review a roll and review and decide). They state the owner's own plan, never the desk's
  instruction.
- The desk has no view of its own. Asked what the desk would do, say that the alert reports the owner's own rule and
  the owner decides. When the owner asks for the choices, the Aegis Structuring Desk prices them on request.
- No urgency words, no praise, no warnings beyond the tool's own text.

## Disclosure

- Every message ends with a footer that says it was posted by an AI assistant and is not personalized investment
  advice. The rules footer ends everything that names a rule, a rulebook version or a verdict:

  ```text
  deterministic rules you adopted · posted by an AI assistant · not personalized investment advice · data delayed 15 min, as of HH:MM UTC
  ```

- The snapshot footer ends only `opening_bell.py`'s public market snapshot, which is rule-free. The lint refuses it
  under a rule id, "your rule", a rulebook version or the verdict words EXIT SIGNAL, WATCH, HOLD and UNAVAILABLE:

  ```text
  public market data · posted by an AI assistant · not a signal and not personalized investment advice · data delayed 15 min, as of HH:MM UTC
  ```

- The structuring footer ends only the Structuring Desk's tickets, its roll analyses and its proposals. Every
  structuring text says, through its lines and this footer, that it was computed from the owner's criteria, posted
  by an AI assistant, a personalized suggestion and not an order, and that the owner decides:

  ```text
  computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of HH:MM UTC
  ```

- There are three footers; never add one of your own, such as a "consult a professional" line.
- Describe the AI's role accurately. The Bots are AI assistants that run deterministic tools; the verdicts and the
  numbers come from those tools. Never claim that a person reviewed a message, that the data is real-time, or that
  the desk watches continuously: the data is delayed 15 minutes and the routines run at fixed slots.
- The approval-level check of the structuring tools is the first reader of `approval_level`: the owner's
  self-attested level in the desk's convention, and the broker's own approval decides. It is never called anything
  but the approval-level check, and the desk still makes no determination of whether a trade fits the owner.
  `attention_thresholds` is recorded for later stages; no tool reads it.

## When the owner asks you to trade

Reply with the boundary sentence, word for word, on its own line:

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

There is nothing else for the desk to do. Never ask for a brokerage login or key and never open a brokerage page.
No Bot but the Aegis Structuring Desk, and only when the owner asks, posts a ticket: it carries no order fields, and
the owner supplies the limit. If the owner wants a rule's level, the Aegis Exit Sentinel can post the
threshold-level sheet or the group's plan card.

## No order fields anywhere; candidates only on request

- Order fields are the trade-side words, an order type, a limit or stop price field, a duration (how long an order
  stands), a route and an account number. None of them appears in any message, file or chat line of any Bot.
- No alert, level sheet, plan card, brief or routine output carries a strike or expiry to move to, or alternatives
  in ranked order. Only the Structuring Desk, only on request, posts candidates in score order and one best
  candidate. The rules and snapshot footers refuse a leg reference, a ranked candidate, an action tag and the first
  line of a structuring answer, and routines refuse the structuring tools.
- A ticket's leg names the contract and its effect on the holdings (`closes your short`, `opens a new long`) in the
  fixed leg grammar: no order field, and never an OCC symbol or a cell the owner typed.
- The threshold-level sheet prints levels, never orders. A loss-side line says
  `alert level, not an order: a limit at this price executes immediately`.
- `desk.toml` keeps `[levels] order_shape_enabled = false`. This release has no order-shape template: when the key is
  true, the sheet still prints alert levels only and says so.
