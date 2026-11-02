# Aegis Desk

**A read-only options desk for Grok Bot and Grok Build.** It watches the owner's open option
positions from a hand-edited `positions.csv`, marks them from Tradier market data, computes
implied volatility, Greeks and P/L with standard-library Python tools, and posts an alert when one
of the owner's own adopted exit rules fires. It never places, modifies or cancels an order.

> Status: **pre-release scaffold (release 0.1.0 in progress).** The desk is not yet usable;
> follow the repository releases.

## What it will be

- **Eight roles** (`agents/`): Desk Lead, Exit Sentinel, Position Control, Book & Risk Analyst,
  Structuring Desk, Events & Calendar Analyst, Rules Steward, Review Desk. Zero broker writers.
- **Twenty-one skills** (`skills/`) in the portable `SKILL.md` format.
- **Stdlib tools** (`scripts/`) that compute every number a Bot ever quotes.
- **Runtimes**: Grok Bot (bootstrap in `SETUP.md`), Grok Build, Claude Code, Cursor, Codex,
  Gemini CLI.

## Controls

- The desk **never trades**. The owner acts in their own brokerage account.
- The only credential on the desk computer is a Tradier **sandbox** market-data token.
- Every number carries a source, its data delay and a UTC time; "unavailable" is a verdict.
- Exit rules are the owner's: versioned, adopted by the owner, cited by id and version in every alert.

Aegis Desk is documentation and tools, not financial advice. Options involve substantial risk.

## Inside the repository

```
SETUP.md     what a Grok Bot follows to build the desk
agents/      role profiles: Bot profile card + full system prompt
skills/      portable skills (aegis-*)
scripts/     stdlib tools and repository checks
routines/    the routine manifest (owner, schedule, tool chain, quiet rule)
template/    the public Grok Bot template contract
docs/        architecture, data contracts, runtime install pages
```

## License and credit

MIT licensed (FairGradient). Repository mechanics are adapted from
[HyperGrok Trading Desk](https://github.com/galleonlabs/hypergrok-trading-desk) by Andrew
Wilkinson and Galleon Labs, used under the MIT license; see `ATTRIBUTION.md`.
