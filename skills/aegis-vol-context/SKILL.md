---
name: aegis-vol-context
description: How to read and relay vol_context.py, for the Aegis Book & Risk Analyst. A symbol's implied volatility on the latest end-of-day snapshot, the median of its solved option legs on the American model, placed among the desk's own earlier recorded days; information only - no rule, alert, ticket or ranking reads it; fewer than 5 earlier days is UNAVAILABLE; never the vendor's volatility, never a judgment of cheap or rich.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: risk
---

# Volatility context

`vol_context.py` places a symbol's implied volatility among the desk's own recorded days. It is information only:
no exit rule, alert, ticket, roll analysis, proposal or ranking reads it, and it never changes a verdict.

## What it reads

- The desk's own end-of-day book snapshots, `data/book/<ET date>.json`, written by `book.py --snapshot`.
- On each day, the symbol's implied volatility is the median of its solved option legs on the American model of
  record (never the vendor's volatility, never a model the desk does not use).
- The latest recorded day's figure is placed among the earlier recorded days: how many of them were lower, and
  their range.

## UNAVAILABLE

- No snapshot holds the symbol, or the latest holds no solved leg for it.
- Fewer than 5 earlier recorded days: a context of fewer days is not shown, never estimated.

## How to relay it

- Quote the lines as printed: the figure, the day it comes from, how many earlier days were lower and their range,
  and the line that says it is information only.
- Say it is the desk's own record of the owner's held contracts, which change as positions open and close, so the days
  are not one contract's history.
- Never call a volatility cheap, rich, high, low, elevated or stretched, never compare it with a market index of
  volatility, and never suggest an action from it.
- The Book & Risk Analyst relays it on request; it posts in no routine.
