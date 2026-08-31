# ADR-0001: Use SearXNG as the search core, and do not modify it

- **Status**: Accepted
- **Date**: 2026-08-30

## Context

The product needs results from many search engines. The options are to build and operate
our own scraper fleet, to adopt an existing meta-search engine, or to use commercial
search APIs.

Commercial APIs (Bing Web Search, Brave Search API, SerpAPI) are the fastest path but
carry per-query cost, require sending user queries to a vendor under a commercial
agreement, and make the "privacy-first" claim depend on that vendor's policy rather than
on our architecture. The brief also requires the project to run without paid
infrastructure.

Building scrapers means writing and maintaining parsers for dozens of engines, each of
which changes markup without notice, plus per-engine ban handling, CAPTCHA detection, and
result scoring. That is years of maintenance work that is not the point of this project.

## Decision

Use **SearXNG** as the meta-search core, run it as an unmodified upstream image, and
build the platform *around* it.

We do not fork or patch SearXNG. Configuration is supplied through `settings.yml` and
environment variables only.

## Consequences

**Positive.** We inherit 272 engine integrations, of which 82 are enabled here,
plus result merging and deduplication,
relevance scoring, and mature per-engine failure handling (`ban_time_on_fail`,
`suspended_times`) that we would otherwise have to write and then keep writing. Upstream
security fixes arrive by changing an image tag. The engineering effort goes into the
layers that are actually ours.

**Negative.** We are bound to upstream's release cadence and its configuration surface. A
behaviour we dislike must be worked around in our own layers or contributed upstream. We
verified that upstream has recently moved from uWSGI to Granian and from Redis to Valkey,
so the configuration surface does move under us, and pinning image digests plus reading
release notes is a real operational obligation.

**Rejected alternatives.** Commercial search APIs — recurring cost, and the privacy
property would be contractual instead of architectural. A custom scraper fleet —
maintenance burden out of all proportion to the value. YaCy — a P2P distributed index,
which is a genuinely different product with different result characteristics.
