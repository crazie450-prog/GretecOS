"""The GTNH wiki cache (WO-8.3, plan 7.8, [H§4], [QA:D5]).

- :mod:`.api`: the MediaWiki ``api.php`` client and its etiquette (one request at a time, a
  descriptive User-Agent, ``maxlag``, at most 50 titles a batch, back-off; an anti-bot gate
  stops it).
- :mod:`.cache`: the licence record, the one-time crawl, the ``recentchanges`` refresh (idle
  maintenance), fetch on demand, and ``w:<page>@<revid>`` citations with attribution.
- :mod:`.cli`: ``gos wiki licence|crawl|refresh|status|show``.

Wiki text is untrusted data (plan 0.1 principle 8) and a personal cache only: it is never
committed, bundled or redistributed.
"""
