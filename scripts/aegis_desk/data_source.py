"""The desk's one market-data source, named once: its label and the words every quote line prints.

New for Aegis Desk release 0.2.0; standard library only, no I/O, no imports from the desk (a leaf module, so the
alert lint can import it without a cycle). The desk reads market data from Alpaca's PAPER environment with a paper
key pair: stocks on the ``iex`` feed (the only stock feed a paper-only account is entitled to) and options on the free
``indicative`` feed, which is 15 minutes delayed and is NOT exchange (OPRA) quotes. The owner chose the free feed,
labelled: wherever a quote is shown, the words say the option quotes are approximate.

* :data:`LABEL` (``alpaca:paper``) is the envelope's ``source`` and the book's ``sources[0].name``.
* :data:`WORDS` is the phrase a quote line prints (``quote grade B, <WORDS>, 15-min delayed, as of 19:31 UTC``), the
  structuring texts' :data:`aegis_desk.structuring_text.SOURCE_WORDS`, the data footer of
  :func:`aegis_desk.quote_cache.footer_line` and of :func:`aegis_desk.book.footer_from_document`, and the opening
  bell's snapshot line. :data:`WORDS_OPRA` replaces it only where the configuration is at hand and says
  ``options_feed = "opra"`` (a paid subscription to exchange quotes): :func:`words_for_feed`. A text built from a
  stored document (an alert from the book's source block) cannot see the feed and keeps the conservative
  :data:`WORDS`: calling exchange quotes approximate overstates the caution, never the quality.
* :data:`ALL_WORDS` is every phrase a tool can print, the alternation the alert lint's leg grammar accepts.

The footer delay stays the configured ``feed_delay_min`` (15 by default and at least 15 with the indicative feed): the
option quotes' real delay, and a conservative bound for the real-time IEX stock quotes.
"""

from __future__ import annotations

from typing import Optional, Tuple

VENDOR: str = "alpaca"
"""``desk_config.VENDOR``; asserted equal by the tests (this module imports nothing from the desk)."""
HOST: str = "paper"
"""``desk_config.HOSTS[0]``; asserted equal by the tests."""
LABEL: str = VENDOR + ":" + HOST
"""The envelope's ``source`` and the book's ``sources[0].name``: ``alpaca:paper``."""
OPTIONS_FEED_INDICATIVE: str = "indicative"
OPTIONS_FEED_OPRA: str = "opra"
WORDS: str = "Alpaca paper (IEX stocks, indicative options: approximate)"
"""The source words of every quote line on the default (free) feed."""
WORDS_OPRA: str = "Alpaca paper (IEX stocks, OPRA options)"
"""The source words when ``desk.toml`` says ``options_feed = "opra"``."""
ALL_WORDS: Tuple[str, ...] = (WORDS, WORDS_OPRA)
"""Every source phrase a tool prints (the lint's alternation)."""


def label(vendor: str = VENDOR, host: str = HOST) -> str:
    """``<vendor>:<host>``: ``alpaca:paper`` for the only configuration ``desk_config`` accepts."""
    return "%s:%s" % (vendor, host)


def words_for_feed(options_feed: Optional[str]) -> str:
    """:data:`WORDS_OPRA` for ``opra``; :data:`WORDS` for ``indicative``, ``None`` and anything else (the
    conservative label: an unknown feed is never called exchange quotes)."""
    return WORDS_OPRA if options_feed == OPTIONS_FEED_OPRA else WORDS


def words_for_label(name: Optional[str]) -> str:
    """The words for a stored ``sources[0].name``: :data:`WORDS` for :data:`LABEL`; another label (a document
    written by an older release, or ``unknown``) prints as ``<Vendor> <host>`` so the line still names its source."""
    text = str(name or "unknown")
    if text == LABEL:
        return WORDS
    vendor, _, host = text.partition(":")
    return ("%s %s" % (vendor.capitalize(), host)).strip()


__all__ = [
    "ALL_WORDS",
    "HOST",
    "LABEL",
    "OPTIONS_FEED_INDICATIVE",
    "OPTIONS_FEED_OPRA",
    "VENDOR",
    "WORDS",
    "WORDS_OPRA",
    "label",
    "words_for_feed",
    "words_for_label",
]
