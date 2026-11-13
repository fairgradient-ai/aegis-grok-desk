---
name: aegis-market-data-budget-and-quality
description: How the read-only Aegis desk gets and judges market data, for the Bots whose tools fetch quotes (Exit Sentinel, Position Control, the Structuring Desk, and the Desk Lead's pre-open run and Opening Bell). The one GET-only client pinned to Alpaca's paper market data, the paper key pair and where it lives, the quote cache with its 200-per-minute ledger and per-run caps, the 15-minute delay, the approximate indicative option quotes and session-aware freshness, quote grades A to F, mid and liquidation marks, chains on demand (the Structuring Desk's for held symbols only, on the owner's request), and why a stale or missing quote is UNAVAILABLE, never a zero.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: market-data
---

# Market data: budget and quality

Every number the desk prints about the market comes from one source: Alpaca's market data, read with a paper key pair
on the free plan. Stocks come from the IEX feed. Option quotes come from the indicative feed: delayed 15 minutes and
not actual exchange (OPRA) quotes, so every quote segment labels them approximate. This skill says how that source is
reached, how its budget is kept, and how each quote is judged before a rule may read it. The tools do
all of this. A Bot never fetches a quote itself, never fixes a grade and never fills a gap with a number of its own.

## The one network path

`scripts/aegis_desk/alpaca_client.py` is the only module in the repository that opens a network connection, and the
repository gate proves it on every run. What it can do is small on purpose:

- every request is a GET, with no request body;
- two hosts are pinned: `data.alpaca.markets` for market data and `paper-api.alpaca.markets`, the paper trading host,
  for three reference reads only; a URL is built inside the client, never taken from outside;
- seven read-only paths are allowed, and nothing else. On the data host: `/v2/stocks/quotes/latest`,
  `/v2/stocks/bars`, `/v1beta1/options/quotes/latest` and `/v1beta1/options/snapshots/<UNDERLYING>` (the underlying
  segment is checked). On the paper host: `/v2/clock`, `/v2/calendar` and `/v2/options/contracts`;
- the live trading host `api.alpaca.markets` is refused;
- a redirect is refused, the environment's proxy settings are ignored, HTTPS verifies the certificate with TLS 1.2 or
  later, and an answer that echoes the key ID or the secret is refused.

No order, position or account path is on the list, so no tool of this desk can reach one.

`desk.toml` must say `host = "paper"`. Any other host, `"live"` included, fails when `desk.toml` loads, so every tool
stops with exit 1 before any request. On a fixture copy with `host = "live"`, `quote_cache.py` printed (synthetic;
the path is the desk root's):

```text
error: <desk root>/desk.toml: [market_data] host must be "paper" (got "live"): the desk holds a paper key only, and the live trading host is refused
```

`desk_init.py` also refuses a live host (exit 1, `live_host`).

## The paper key pair: the owner's, never in chat

The desk holds one credential: an Alpaca paper key pair, a key ID and its secret. It cannot reach a live brokerage
account. It could place paper orders on the paper trading host, which the desk's GET-only code cannot do.

- The owner adds both halves through the secure secret card as the environment variables `APCA_API_KEY_ID` and
  `APCA_API_SECRET_KEY` (the names in `desk.toml` `[market_data] key_env` and `secret_env`).
- The fallback is the file `/workspace/.aegis-secrets/alpaca.key` (`credentials_file` in `desk.toml`): the key ID on
  line 1 and the secret on line 2. It must be a regular file with no group or other permission bits (`chmod 600`);
  otherwise the client refuses it as `credential_file_mode`.
- The client uses the two environment variables when both are set, else the file; it never takes one half from each.
  No tool prints either half, and its error messages never carry them.
- A Bot never asks for the key in chat, never reads it aloud, never copies it into a file and never stores it in a
  memory. If the owner pastes it in chat, the Bot says the key is now exposed and asks the owner to regenerate it in
  the Alpaca dashboard (Paper Trading) and add the new pair through the secret card.

With no key pair, a live tool stops before any request. On a fixture copy with no key, `quote_cache.py` printed
(synthetic):

```text
UNAVAILABLE: no market-data key: add the Alpaca paper key ID and secret through the secret card (or the acknowledged credentials file)
```

The desk doctor checks the key two ways. Check 4 (`key_source`) names where the pair came from (the two variable
names, or the file), never a value. Check 5 (`paper_key`) runs only in the full doctor, `python3
scripts/desk_doctor.py --desk-root /workspace/aegis-desk`: it makes one GET of `/v2/clock` on the paper trading host.
Alpaca's data host answers live keys too, so only the paper host can tell a paper key from a live one: it refuses a
key from another environment with a 401 or 403 (Alpaca's documented mismatch error, not a published paper/live
test). An answer PASSes `paper key verified`; a 401 or 403 FAILs with `not a paper key: remove it and add a paper
key`. The pre-open routine runs the quick doctor, which has no paper-key check; the live Opening Bell's contract
listing goes to the same paper host and prints `UNAVAILABLE: not a paper key: ...` for a live key.

## The cache, the ledger and the per-run caps

`scripts/aegis_desk/quote_cache.py` sits between every tool and the client. Its files live under `data/cache/` in the
desk root:

| File | What it holds |
| --- | --- |
| `quotes.json` | the latest vendor answer per symbol, with the instant it was fetched |
| `ledger.jsonl` | one line per request attempt, success or failure |
| `chains/<SYMBOL>__<date>.json` | an option chain, fetched only on request |
| `expirations/<SYMBOL>.json` | a symbol's listed expirations, served again all day on the same ET date |
| `history/<SYMBOL>__<start>__<end>.json` | daily bars, which `csv_recon.py` settles expired legs against |

The rules it keeps:

- A cached quote younger than `quote_ttl_min` (5 minutes in the `desk.toml` that `desk_init.py` writes) is served
  without a request.
- Quotes go out in batches of at most 50 symbols.
- No new fetch starts once a run has made 40 requests, and a run fetches at most 4 chains. Every request counts, not
  every call: a quotes batch that holds both stocks and options is 2 requests (one per feed), and a chain is at least
  2 (the contract listing, then the snapshots; each page of a long answer is one more). A chain starts only while
  those 2 still fit under the 40; a paginated answer can carry the call that is under way past 40.
- The ledger is desk-wide. Every tool of the desk counts against one rolling 60-second window of 200 requests (the
  free plan's market-data limit), minus a headroom of 20.
- A quota error stops fetching for the rest of the run. So does a credential or plan error (`auth`, `subscription`,
  `no_credential`, `credential_file_mode`). Any other failure marks its batch and the next batch is still tried.

A symbol that could not be served carries one of these reasons: `quota_guard`, `per_run_cap`, `market_closed` or
`fetch_failed:<status>`. A symbol the vendor did not quote (an index, an expired or unknown contract) is
`fetch_failed:not_returned`. A synthetic fixture run of `quote_cache.py SPY WMT` at 19:50 UTC, then
again two minutes later:

```text
SPY 648.10/648.14 648.12 as-of 14:31:12 ET (age 18 min) fetch
WMT 104.20/104.26 104.23 as-of 14:31:12 ET (age 18 min) fetch
session open (2026-11-03 14:50:00 ET); requests 1; 60-s window used 1, left 179
Alpaca paper (IEX stocks, indicative options: approximate), delayed 15 min, as of 19:31 UTC
```

```text
SPY 648.10/648.14 648.12 as-of 14:31:12 ET (age 20 min) cache
WMT 104.20/104.26 104.23 as-of 14:31:12 ET (age 20 min) cache
session open (2026-11-03 14:52:00 ET); requests 0; 60-s window used 0, left 180
Alpaca paper (IEX stocks, indicative options: approximate), delayed 15 min, as of 19:31 UTC
```

The second run spent nothing: both quotes came from the cache.

## Delay and freshness, by session

The indicative option feed is delayed 15 minutes, and the desk treats every quote as 15 minutes delayed
(`feed_delay_min = 15`, the conservative bound; IEX stock quotes may be fresher). Every quote segment of a desk
message names the source and the delay, for example `quote grade B, Alpaca paper (IEX stocks, indicative options:
approximate), 15-min delayed, as of 19:31 UTC`. With `options_feed = "opra"` (a paid subscription) the words read
`Alpaca paper (IEX stocks, OPRA options)`.

A quote side is fresh when its own stamp is within the freshness bound: `feed_delay_min + quote_ttl_min + grace_min`,
which is 15 + 5 + 5 = 25 minutes with the values `desk_init.py` writes. Each side, bid and ask, is judged on its own
stamp. The reference instant depends on the session:

| Session | Judged against | Label |
| --- | --- | --- |
| open (regular hours, or an early-close day before 13:00 ET) | the run instant | `as of HH:MM:SS ET` |
| closed (a trading day after its close) | that session's close | `as of close HH:MM ET` |
| pre (a trading day before 09:30 ET) | the previous close | `as of prior close YYYY-MM-DD HH:MM ET` |
| weekend or holiday | nothing: no fetch at all | `NYSE closed` |

A postmarket or premarket print is not a closing quote. On a weekend or a holiday no tool fetches a quote, and every
quote is grade F; only the Saturday reconciliation still reads daily bars. A synthetic fixture run of `quote_qc.py` on
Thanksgiving, 2026-11-26 15:00 UTC:

```text
NYSE closed (Thanksgiving Day): no quote fetch; nothing is usable for rules today
```

## Grades A to F, and what each lets a rule do

`quote_qc.py` grades every quote. The spread is `(ask - bid) / mid x 100`, printed to two decimals, and the grade is
decided on the printed value, so the number the owner sees and the grade always agree.

| Grade | Condition | Use |
| --- | --- | --- |
| A | both sides fresh, bid above 0, spread at most 2 % | every rule |
| B | spread at most 5 % | every rule |
| C | spread at most 10 % | every rule |
| D | spread at most 25 %, or exactly one side stale | every rule with both sides fresh; with one side stale there is no mid, so only the fresh side can be read |
| E | bid of 0, or spread above 25 % | display only, except that a take-profit may read the fresh side that can only delay it |
| F | a side missing, undated or stale beyond the bound, a crossed market, or a closed day | nothing |

Grades A to D are "usable for rules". What a grade E quote still allows is in `aegis-book-marking-and-greeks-honesty`.

A synthetic fixture run, `quote_qc.py SPY WMT WMT261120C00105000` on the regular set at 19:50 UTC. The columns are the
symbol, the grade, the status, bid/ask, the spread, the mid, the bid a long leg is marked at, the ask a short leg is
marked at, and the label:

```text
SPY A fresh 648.10/648.14 0.01% 648.12 648.10 648.14 as of 14:31:12 ET
WMT A fresh 104.20/104.26 0.06% 104.23 104.20 104.26 as of 14:31:12 ET
WMT261120C00105000 B fresh 3.35/3.50 4.38% 3.425 3.35 3.50 as of 14:31:12 ET
session open; freshness bound 25 min
Alpaca paper (IEX stocks, indicative options: approximate), delayed 15 min, as of 19:31 UTC
```

The same symbols on the stale set, stamped 60 minutes before the run:

```text
SPY F stale 648.10/648.14 0.01% - - - as of 13:50:00 ET
WMT F stale 104.20/104.26 0.06% - - - as of 13:50:00 ET
WMT261120C00105000 F stale 3.35/3.50 4.38% - - - as of 13:50:00 ET
session open; freshness bound 25 min
Alpaca paper (IEX stocks, indicative options: approximate), delayed 15 min, as of 18:50 UTC
```

## Marks: mid and liquidation; the last trade is never a mark

- The mid is `(bid + ask) / 2`.
- The liquidation mark is the bid for a long leg and the ask for a short leg: what closing the position would face.
- A leg's `mark_source` is `mid` only for a grade A to D quote with both sides fresh. Every P/L figure and every loss
  test needs it.
- With exactly one side stale, the desk never invents a mid from the fresh side.
- The last trade price is never a mark. No model price feeds a rule or a P/L figure.

## Chains on demand; index symbols

Each tool fetches one batch of quotes per run, and no routine step fetches a chain. A chain is fetched only when
someone asks for it, and the command needs at least one symbol beside the chain:

```text
python3 scripts/quote_cache.py SPY --chain SPY --expiration 2026-12-18 --desk-root /workspace/aegis-desk
```

`opening_bell.py` makes four requests: the quote, the expirations (a contract listing) and one chain (its contract
listing, then its snapshots); more only when a listing pages.

The Structuring Desk's tools fetch chains only for the underlying of the group the owner named, a symbol held in
`positions.csv`, and only on the owner's request; no routine runs them. Each run keeps to the same caps of 40
requests and 4 chains and to the desk-wide ledger:

- a roll analysis or a proposal: one quotes batch of the group's symbols, the underlying's expirations, then the
  standard monthly chains inside the owner's window (at most four). The WMT roll analysis of the fixture made 7
  requests: the quotes (2, stocks and options), the expirations (1) and two chains (2 each);
- a ticket: one quotes batch, and for a ROLL the one chain of the replacement's expiration (4 requests in the
  fixture);
- an expiration of the window that cannot be served (the quota, the cap, a vendor failure) makes the answer
  UNAVAILABLE, never a best candidate on partial data; an empty expiration list from the vendor is never cached and
  counts as missing;
- a new leg is priced only when both sides are fresh with a mid, at quote grade A to D, and it is a standard contract
  of the underlying; a spread above `max_spread_pct` is still priced and falls OUTSIDE_BANDS `max_spread_pct`. A
  contract the chain lists more than once with different quotes is not priced. A held leg being closed needs only its
  conservative fresh side.

The desk treats index options as ones the data source cannot quote (Alpaca does not confirm that its free indicative
feed serves them to a paper account), and it ignores the vendor's Greeks and implied volatility. The desk computes IV and Greeks itself from the quotes it
has, and says those quotes are approximate. An index underlying in `positions.csv` is reported by `csv_recon.py` as the warning
`UNQUOTABLE_INDEX`; its legs cannot be marked.

## What a stale or missing quote becomes: UNAVAILABLE

A rule that needs a quote the desk cannot use has no answer, and the desk says so. It never reads a stale quote as
fresh, a missing one as zero, or an unanswered rule as HOLD. From a synthetic book check on the stale set at 19:50 UTC
(the first two lines of one group):

```text
UNAVAILABLE AG-20261020-01 | WMT covered call | rules 1.0.0, sha cc09859c: no verdict while an input is unavailable; this is not HOLD | quote grade F, Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 18:50 UTC, run 45 min late | not an instruction to trade; you act in your brokerage account.
unavailable: your rule TP80: row 4 WMT261120C00105000: quote grade F (stale): no fresh ask
```

The banner re-posts on every book-check run until the owner acknowledges it or fresh quotes return.

## Running the tools interactively

These diagnostics take `--desk-root /workspace/aegis-desk` and read the same cache the routines use:

```text
python3 scripts/quote_qc.py WMT WMT261120C00105000 --desk-root /workspace/aegis-desk
python3 scripts/quote_cache.py WMT --desk-root /workspace/aegis-desk
```

Their output is diagnostic, not a desk message. When the owner asks for it, the Bot quotes it unchanged in a fenced
block headed by the exact command, and adds no number of its own. Every interactive request counts against the same
60-second window as the routines, so a Bot never runs these in a loop. `docs/TOOLS.md` lists every flag and exit
code.
