# FAQ

Short answers for the owner, from what this release does. The details are linked.

## What does setup ask me for?

One thing. You paste the Start prompt from the [README](../README.md#start) into a new Grok Bot. The Aegis Desk Lead
builds the desk, shows you a demo on a sample book, and asks you for one thing: a free Tradier sandbox market-data
token, added through the secure secret card, never pasted in chat. It checks the token with one live market snapshot
and returns a receipt. Nothing else is asked in the first run.

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

## How do I get a Tradier sandbox token?

The desk reads delayed market data from Tradier's sandbox, its free practice (paper trading) environment. You need a
free Tradier account, then its sandbox token. Tradier no longer offers a developer-only sandbox without an account.

1. **Open a Tradier account.** Go to [tradier.com](https://tradier.com) and choose Sign up. Tradier's API FAQ says
   API access comes with the account at no extra cost, and that the sandbox is free. Whether the account must be
   funded first is not confirmed. For personal use you need no Developer Portal.
2. **Find the tokens.** Log in at [web.tradier.com/login](https://web.tradier.com/login), click your name in the top
   right corner and choose API Access, or go straight to
   [web.tradier.com/user/api](https://web.tradier.com/user/api). That page manages the tokens for both the live and
   the sandbox environments. On a phone, open the same page in your phone's browser (not in a Bot's browser); a path
   inside Tradier's mobile app is not confirmed.
3. **Copy the sandbox token, and only that one.** The production token, under Production Account Access, sits on the
   same page and comes first: leave it alone. Tradier's help calls the sandbox section Sandbox Account Access (Paper
   trading) and describes enrolling in paper trading there if no sandbox token is listed yet; we could not confirm
   those labels on the page itself. The token is hidden: tap the eye icon to show it and the copy icon to copy it.
4. **Add it through the secure secret card** when the Aegis Desk Lead asks, never in chat. Tradier's advice: "Treat
   your API tokens like passwords ... Anyone with access to your token can make API calls and take actions on your
   account."

The two tokens are separate. A sandbox token works with `https://sandbox.tradier.com/v1/` and a production token with
`https://api.tradier.com/v1/`; Tradier lists using one with the other's endpoints as a cause of a 401 error. The desk
talks only to the sandbox host, and its doctor checks the token with one read of the sandbox clock: a 401 or 403
fails with `not a sandbox token: remove and rotate`.

## Can the desk place a trade for me?

No. This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

No tool in this repository sends anything but a read-only market-data request, and
`scripts/check_no_write_paths.py` proves it on every gate run. No Bot holds a brokerage credential; the only
credential is a Tradier sandbox market-data token, which cannot reach a live account. When you ask a Bot to trade, it
answers with the boundary sentence above: the decision and the order are yours, in your brokerage account. When you
ask the Aegis Structuring Desk for a ticket, it lists the legs with their quotes and carries no order fields: you
supply the limit and type it into your brokerage account yourself, if you decide to act. See
[SECURITY.md](../SECURITY.md).

One honest gap: the sandbox token could place paper orders on Tradier's practice host. The Auto-review rule that
makes Grok Bot ask you before any order, brokerage write or brokerage sign-in is added when you say "add my
positions", and it comes first whichever later phrase you say first. Between the first run and then, the desk holds
only that sandbox token, which cannot reach a live account, and the desk's code still has no way to write: its
market-data client sends GET requests only, and the repository gate proves it. If you paste the production token by
mistake, the live check in the first run fails with `auth:` and the Lead asks you to delete it from the secret card at
once; until you do, no Ask-first rule guards it.

## Why is the data 15 minutes late, and what does the desk do about it?

The desk uses the Tradier sandbox feed, which is delayed 15 minutes. Every message names its source, the delay and
the UTC time of its quotes, for example `Tradier sandbox, 15-min delayed, as of 19:31 UTC`. A quote counts as fresh
during the session when it is no older than 25 minutes (the 15-minute delay, a 5-minute cache and 5 minutes of
grace); an older quote makes the rule that needs it UNAVAILABLE. A take-profit rule reads the conservative side of the
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
rule that needs the index level is UNAVAILABLE. Tradier's older documentation said the sandbox serves no index data;
what its current documentation says was not confirmed. An AM-settled root is named in `research/calendar.toml` under `am_settled_roots`; its
settlement value is yours to record there, and until you do, the reconciliation says `SETTLEMENT_UNKNOWN`. Sourcing
those values is planned for Stage 2 (not in this release).

## Can I use production data?

Not in this release. A production host is refused, with the message `production market data is not supported before
the market-data proxy (Stage 3); use the sandbox host`. A production token can trade, so it would leave the
guarantee. A local read-only proxy that would hold it outside the Bots' reach is planned for Stage 3 (not in this release).

## How are Greeks computed?

The desk computes them itself and never takes them from the feed (Tradier's older documentation said the sandbox
serves no Greeks; its current documentation was not confirmed): implied volatility from the quote's mid
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
