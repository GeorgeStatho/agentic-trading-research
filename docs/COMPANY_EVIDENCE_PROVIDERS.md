# Company evidence providers

Capability review completed 2026-09-28, before adapter implementation.

| Source | Universe / metrics | Time and revisions | Cost / limits | Storage |
| --- | --- | --- | --- | --- |
| SEC submissions and filing archives | SEC filers; periodic reports and issuer earnings releases furnished as 8-K/6-K exhibits; guidance where explicitly stated | Acceptance timestamp when supplied; filing date otherwise. Accession identifies a filing; amendments and changed downloaded content remain separate versions. | Public, no API key; declared contact User-Agent required. SEC ceiling is 10 requests/second across machines; this collector defaults to one/second and 500/day. | Retain downloaded public evidence internally with URL and content hash. Public access is not a blanket copyright license for issuer material. |
| SEC companyfacts | Entity-wide standard US-GAAP tags: revenue, net income, diluted EPS, gross/operating income, operating cash flow, cash, current/long-term debt components | Exact reported start/end dates, filing accession and filed date. No assumption that fiscal-year labels describe comparative facts. Later observations never backdate local knowledge. | Same SEC budget; one companyfacts request per issuer per refresh | Immutable API response snapshots and normalized fact excerpts. Custom tags and segment facts are outside initial coverage. |
| Configured issuer RSS releases | Explicit company/feed/host mapping; prose guidance and earnings-event JSON-LD if present | RSS publication timestamp; local observation fallback marked unknown. Changed URL content retained as a revision. No guaranteed historical revision feed. | No universal quota or license. Opt-in per source after verifying access/storage terms; one request/second and bounded daily budget by default. | Only configured sources whose operator has confirmed internal caching permission. No redistribution feature. |
| Optional consensus adapter | Provider-dependent analyst expectations, period, basis, currency and source required | Must supply dated observations and source spans; no historical revision capability assumed | No commercial provider selected or purchased. Disabled unless explicitly injected by an integration. | Integration must establish contractual retention rights before enabling. |

Primary documentation checked: [SEC API definitions](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [SEC fair access](https://www.sec.gov/about/developer-resources), and [EDGAR download paths, identifiers, corrections and ticker mappings](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data).

Initial coverage is deliberately explicit: US-GAAP standard tags, recent filings,
and guidance sentences that state their period, metric, unit and accounting basis.
Unsupported tables/prose remain documents plus extraction gaps. Missing consensus
does not imply zero expectations or an earnings surprise. Direct issuer feeds are
optional; SEC furnished release exhibits provide the default issuer-release path.
