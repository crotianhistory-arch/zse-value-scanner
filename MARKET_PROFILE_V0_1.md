# Commercial market profile v0.1

This layer answers a narrower question than peer classification:

> Where should we search for companies that may compete with the target company?

It separates evidence that is often incorrectly conflated:

1. **Business scope evidence** — e.g. Power Transmission and Distribution is 74.6% of KONČAR Group 2025 revenue.
2. **Group geography evidence** — e.g. Germany is a major reported export market.
3. **Order-intake evidence** — evidence that a country is commercially active or strategically important.
4. **Direct activity-market evidence** — a reported project explicitly linking a product/activity to a country/customer.

The central rule is:

**Group geography is not automatically segment geography.**

If KONČAR reports EUR 188.3m of German group revenue, the system may use Germany as a search market. It must not state that EUR 188.3m was transformer revenue unless a source explicitly provides that linkage.

A dominant business scope can be combined with reported market evidence only as `H1_SEARCH_HYPOTHESIS`. Direct contract/project evidence is kept separately and can establish a narrower activity-market link.

No competitor, peer, similarity, valuation or database decision is made by this layer.
