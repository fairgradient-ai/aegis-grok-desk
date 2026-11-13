# FAQ

Short answers for the owner, from what this release does. The details are linked.

## What does setup ask me for?

One thing. You paste the Start prompt from the [README](../README.md#start) into a new Grok Bot. The Aegis Desk Lead
builds the desk, shows you a demo on a sample book, and asks you for one thing: a free Alpaca paper key (a key ID and
its secret), added through the secure secret card, never pasted in chat. It checks the key with one live market
snapshot and returns a receipt. Nothing else is asked in the first run.

Everything else waits until you say so:

| Say | What happens | What you do |
| --- | --- | --- |
| "add my positions" | the two safety rules and the connected-apps check first, then the desk settings with suggested answers, then your positions | paste two Auto-review rules, answer one question about connected apps, confirm the settings, describe your positions in your own words and approve the drafted lines |
| "adopt my exit rules" | the example rules, ready to adopt | keep them or change some, then tap approve and type the adoption phrase |
| "turn on monitoring" | a check that routines work on your plan, the daily routines created paused, one test run, then switched on | turn on Notifications and set the Timezone on three Bots, paste the Allow rule |
| "set up the structuring desk" (optional) | the criteria the Structuring Desk scores your choices by | answer its questions and tap approve |

Say "add my positions" first. Whichever phrase you say first, the Lead checks what that step sets up (the two
Ask-first rules, the connected-apps check and the desk settings) and does any missing part before anything else.
[SETUP.md](../SETUP.md) section 9 has each step.

## How do I get an Alpaca paper key?

The desk reads delayed market data from Alpaca with a paper key: the key of Alpaca's free practice (paper trading)
account, which anyone can open with an email address, with no funding and no brokerage application (Alpaca's
documentation, read 2026-11-13).

1. **Open a paper account.** Go to [alpaca.markets](https://alpaca.markets) and sign up with your email address. A
   paper-only account starts with simulated money and costs nothing; the free plan is Alpaca's default.
2. **Switch to Paper Trading.** Log in to the dashboard and make sure the environment switch says Paper Trading,
   not Live. Do this on a computer or in your phone's browser, not in a Bot's browser.
3. **Generate the key.** In the API Keys panel on the right side of the dashboard, choose Generate New Key. Alpaca
   shows the key ID and the secret. The secret is shown only once: if you lose it, generate a new pair, which stops
   the old one working.
4. **Add both through the secure secret card** when the Aegis Desk Lead asks, never in chat: the key ID as
   `APCA_API_KEY_ID` and the secret as `APCA_API_SECRET_KEY`.

Paper and live keys are separate. Paper key IDs usually start with `PK` and live ones with `AK`, but Alpaca calls that
a visual hint, not a security check. The desk's checks send your key to Alpaca's paper trading host, which refuses
keys from another environment with a 401 or 403, so a live key fails them (Alpaca documents the mismatch error; it
does not publish a separate paper/live test). The first run's market snapshot lists option contracts on that host and
fails with `UNAVAILABLE: not a paper key: ...`; the desk doctor reads that host's clock once and fails with `not a
paper key: remove it and add a paper key`. Alpaca's market-data host answers live keys too, so only the paper-host
step tells the two apart. Copy the key from the Paper Trading dashboard only.

What the free plan gives the desk:

- **Stocks:** the IEX exchange's own quotes, the only stock feed a paper-only account is entitled to.
- **Options:** Alpaca's indicative feed. Its option quotes are delayed 15 minutes and are not actual exchange (OPRA)
  quotes; Alpaca describes them as indicative and modified. Every desk message that shows a quote labels them
  approximate. Real exchange option quotes need Alpaca's paid subscription; the desk's `options_feed = "opra"`
  setting is for a subscriber only.
- **Rate:** 200 market-data requests a minute, shared by every tool of the desk.

A licence note: Alpaca's market data is licensed for your personal use. An Alpaca staff reply on its forum says it
may not be provided, or anything derived from it, to third parties. The desk shows it only to you, the owner; if
anyone else, or a business, will see the desk's messages, ask Alpaca for written confirmation first.

## Can the desk place a trade for me?

No. This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

No tool in this repository sends anything but a read-only market-data request, and
`scripts/check_no_write_paths.py` proves it on every gate run. No Bot holds a live brokerage credential; the only
credential is an Alpaca paper key pair, used only to read market data, which cannot reach a live account. When you ask a Bot to trade, it
answers with the boundary sentence above: the decision and the order are yours, in your brokerage account. When you
ask the Aegis Structuring Desk for a ticket, it lists the legs with their quotes and carries no order fields: you
supply the limit and type it into your brokerage account yourself, if you decide to act. See
[SECURITY.md](../SECURITY.md).

One honest gap: the paper key could place paper orders on Alpaca's paper trading host. The Auto-review rule that
makes Grok Bot ask you before any order, brokerage write or brokerage sign-in is added when you say "add my
positions", and it comes first whichever later phrase you say first. Between the first run and then, the desk holds
only that paper key, which cannot reach a live account, and the desk's code still has no way to write: its
market-data client sends GET requests only, and the repository gate proves it. A live key pasted by mistake fails the
first run's market snapshot with `UNAVAILABLE: not a paper key: ...`, because its contract listing is read on Alpaca's
paper trading host, which refuses keys from another environment; the desk doctor's paper check rests on the same
refusal from "add my positions" on. Until you delete a live key from the secret card, no Ask-first rule guards it, so
copy only from the Paper Trading dashboard.

## Why is the data 15 minutes late, and what does the desk do about it?

The desk uses Alpaca's free data: IEX stock quotes (real time, from the IEX exchange only) and indicative option
quotes, which are delayed 15 minutes and approximate (not exchange quotes). The desk treats every quote as 15 minutes
delayed, the conservative bound. Every message names its source,
the delay and the UTC time of its quotes, for example `Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC`. A quote counts as fresh
during the session when it is no older than 25 minutes (the 15-minute delay, a 5-minute cache and 5 minutes of
grace); an older quote makes the rule that needs it UNAVAILABLE. An implied volatility is computed only when the
option quote and its stock quote are stamped within 60 seconds of each other; if the delayed option quotes turn out
to be stamped 15 minutes behind the real-time stock quotes, those lines say UNAVAILABLE rather than mix the two
([PROVENANCE.md](PROVENANCE.md) records this open item). A take-profit rule reads the conservative side of the
quote, so a wide spread can only delay it. For real-time watching, use your broker's alerts at the prices on the
threshold-level sheet.

## What does UNAVAILABLE mean?

That the desk cannot decide, because an input the rule needs is missing or stale. It is a verdict, never a quiet
negative: the message says `no verdict while an input is unavailable; this is not HOLD` and names each missing input.
The banner re-posts on every book check until you acknowledge it with `ack.py ack <signal id>`. Check your broker
directly while it stands.

## The desk went quiet. Is it running?

Silence between book checks means nothing changed. Four messages always come on a trading day: the Morning Desk Sheet
at 07:30 ET, the pre-close line at 15:35 ET, the threshold-level sheet at 16:35 ET and the EOD Desk Brief at 17:10 ET.
The dead-man rule: no Morning Desk Sheet by 08:00 ET, or no pre-close
line by 16:15 ET, means the desk is not running. Rely on your broker's alerts set from the last threshold-level sheet,
and answer any prompt that asks to keep the routines running. Missed runs are never replayed. The 17:10 EOD Desk
Brief names every late and missed run. See [MONITORING.md](MONITORING.md).

## How do I change a rule?

Tell the Aegis Desk Lead what you want changed. It copies your adopted rulebook to a candidate, changes only what you
stated, and shows the effect with `rules_diff.py` (it writes nothing). You tap approve and type the phrase
`adopt rules X.Y.Z`, and the Lead runs `rules_adopt.py --owner-phrase "adopt rules X.Y.Z"`. Adoption waits for the
close unless you choose to adopt during market hours, which adds `--allow-rth` and is recorded. The next book
check posts the digest once, and CONFIG CHANGED stands until you tap `ack.py config --owner-tapped`.
The [exit rules page](EXIT_RULES.md) has the details.

## How do I record a trade, a close, an expiry or an assignment?

Tell Aegis Position Control what you did in your broker, in your own words, for example "I sold 2 NVDA 20-Nov 150
calls at 3.20 against my 200 shares". It drafts the exact `positions.csv` lines; you approve them, and it saves them
exactly as approved, or you apply them yourself. For an expiry or an assignment, `csv_recon.py
--diff` proposes the lines after the expiry, inferred from the official close: check them against your broker before
you apply them. After any change, CONFIG CHANGED posts until you tap `ack.py config --owner-tapped`. See
[POSITIONS_CSV.md](POSITIONS_CSV.md).

## What happens in expiry week?

Every weekday the Morning Desk Sheet carries the docket of contracts due this week. At 15:20 ET the day before a held
contract's last trading day, Aegis Position Control posts the T-1 PREVIEW; on the day itself, the FINAL CALL with the
act-by times (a short option's session close, or your broker's exercise cutoff when earlier; a long option's
broker cutoff), the pin tier and the dividend tier. On Saturday at 09:30 ET it posts
the inferred outcomes and the number of proposed edits; `csv_recon.py --diff` shows the lines. An outcome near the strike is `PIN_UNCERTAIN`: confirm with your broker.
See the skill [aegis-expiry-and-assignment-protocol](../skills/aegis-expiry-and-assignment-protocol/SKILL.md).

## Index options and AM settlement?

The desk treats an index underlying as one it cannot quote: an index row gets the warning `UNQUOTABLE_INDEX`, and a
rule that needs the index level is UNAVAILABLE. Alpaca serves index option data on its paid exchange feed; whether
its free indicative feed serves index options to a paper account is not confirmed, so the desk does not rely on it. An AM-settled root is named in `research/calendar.toml` under `am_settled_roots`; its
settlement value is yours to record there, and until you do, the reconciliation says `SETTLEMENT_UNKNOWN`. Sourcing
those values is planned for Stage 2 (not in this release).

## Can I use live-account data?

No. The desk holds a paper key only, and `desk.toml` must say `host = "paper"`: any other host, `live` included,
stops `desk.toml` from loading, with the message `[market_data] host must be "paper" (got "live"): the desk holds a
paper key only, and the live trading host is refused`. A live key can trade, so it would leave the guarantee.

## How are Greeks computed?

The desk computes them itself and never takes them from the feed (Alpaca's option snapshots carry Greeks and implied
volatility computed from indicative quotes; the desk ignores them): implied volatility from the quote's mid
with the generalised Black-Scholes-Merton model, and European Greeks in desk units (theta per calendar day, vega per
volatility point). The dividend yield comes from `research/calendar.toml`, or 0 with a warning on the leg. Early
exercise is not modelled in Stage 1; an American lattice is planned for Stage 2 (not in this release). The
Structuring Desk prices its candidates the same way and prints that it does. See the skill
[aegis-book-marking-and-greeks-honesty](../skills/aegis-book-marking-and-greeks-honesty/SKILL.md).

## Is there a Grok Bot template?

Not a published one. `template/grok-bot.json` is the template contract, and its status is `unpublished` in this
release. Template import has been reported to drop skills, so the paste prompt in the README is the way in, and the
bootstrap reads every skill back against the checkout. When a template is published, its share link appears here and in the
README. See [GROK_BOT_TEMPLATE.md](GROK_BOT_TEMPLATE.md).

## How much usage does it take?

Every routine run spends weekly Grok usage. The lean profile is 41 runs per week; the active profile, with four more
book checks a day, is 61 (`routine_audit.py --budget`; holidays not deducted). No tool measures the usage of a run. The
Aegis Structuring Desk runs no routine, so it adds no routine runs; each of its answers is one request of yours.

## What are my choices for a position?

Ask the Aegis Structuring Desk, in its chat or in the Floor. It answers only your own request, with one of three tool
texts: a ticket (closing the option legs, unwinding the shares too, letting a short option go to expiry, closing a
long put for its value, or replacing a leg you name), a roll analysis for a covered call's short call, or a covered
call, protective put or collar on shares you hold. It needs your criteria in `structuring.toml` first: say "set up the
structuring desk" (SETUP step 9). Without them it shows nothing. The criteria, the outputs and the limits are in [STRUCTURING.md](STRUCTURING.md).

## Is this investment advice?

The alerts: no. The rules are yours: you adopt each one, and an alert says that your own rule fired, with the numbers
that made it fire and the plan you wrote down. Alerts carry no order fields, no strikes or expiries to move to and no
ranked alternatives. Every alert says it was posted by an AI assistant and is not personalized investment advice. How
the desk stays on the informational side for alerts, and why that is not legal advice, is in
[rules/examples/README.md](../rules/examples/README.md).

The Aegis Structuring Desk is different, and the desk says so. Only when you ask, its tools take a position you hold,
score the candidates by the objective and criteria you set in `structuring.toml`, and name the highest-scoring one
with its reasons. That is a personalized suggestion, computed by deterministic tools and posted by an AI assistant,
and its footer says exactly that. It is not an order, not a forecast and not a judgment that a trade fits you; the
desk does not see your cash, your other holdings, your objectives or your taxes. You decide, you supply the limit and
you act in your brokerage account. Whether your use is investment advice is for your counsel; the project's counsel
review is pending ([RELEASE.md](RELEASE.md), step 4). Options involve substantial risk.

## What does the desk not do?

It proposes no new symbols, no spreads and no uncovered positions, and it places no orders. It prices choices and
proposals only when you ask, and only for positions you hold. It does not read news, run backtests, optimize, or
handle taxes.
