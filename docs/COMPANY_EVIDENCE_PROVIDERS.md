# Company evidence providers

Capability review completed 2026-09-28, before adapter implementation.

| Source | Universe / metrics | Time and revisions | Cost / limits | Storage |
| --- | --- | --- | --- | --- |
| SEC submissions and filing archives | SEC filers; periodic reports and issuer earnings releases furnished as 8-K/6-K exhibits; guidance where explicitly stated | Acceptance timestamp when supplied; filing date otherwise. Accession identifies a filing; amendments and changed downloaded content remain separate versions. | Public, no API key; declared contact User-Agent required. SEC ceiling is 10 requests/second across machines; this collector defaults to one/second and 500/day. | Retain downloaded public evidence internally with URL and content hash. Public access is not a blanket copyright license for issuer material. |
| SEC companyfacts | Entity-wide standard US-GAAP tags: revenue, net income, diluted EPS, gross/operating income, operating cash flow, cash, current/long-term debt components | Exact reported start/end dates, filing accession and filed date. No assumption that fiscal-year labels describe comparative facts. Later observations never backdate local knowledge. | Same SEC budget; one companyfacts request per issuer per refresh | Immutable API response snapshots and normalized fact excerpts. Custom tags and segment facts are outside initial coverage. |
| Configured issuer RSS releases | Explicit company/feed/host mapping; prose guidance and earnings-event JSON-LD if present | RSS publication timestamp; local observation fallback marked unknown. Changed URL content retained as a revision. No guaranteed historical revision feed. | No universal quota or license. Opt-in per source after verifying access/storage terms; one request/second and bounded daily budget by default. | Only configured sources whose operator has confirmed internal caching permission. No redistribution feature. |
| Yahoo consensus (`Webscraping/company_evidence/yahoo_consensus.py`, on by default since 2026-10-28) | Next-report EPS and revenue estimates (average, plus high when consistent) and the expected earnings date, from `yfinance.Ticker(symbol).calendar` | One snapshot per refresh, dated at fetch time. No history of revisions; an unchanged calendar is the same stored document | Free and unofficial (Yahoo's terms are a grey area); approved for this personal paper-trading project only. One `calendar` read per company per refresh, throttled by the collector's provider TTL | Stored internally like other evidence documents. Swap in a licensed adapter (the `ConsensusAdapter` protocol in `company_evidence/models.py`) before any commercial use |

Primary documentation checked: [SEC API definitions](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [SEC fair access](https://www.sec.gov/about/developer-resources), and [EDGAR download paths, identifiers, corrections and ticker mappings](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data).

Initial coverage is deliberately explicit: US-GAAP standard tags, recent filings,
and guidance sentences that state their period, metric, unit and accounting basis.
Unsupported tables/prose remain documents plus extraction gaps. Missing consensus
does not imply zero expectations or an earnings surprise. Direct issuer feeds are
optional; SEC furnished release exhibits provide the default issuer-release path.

## Yahoo consensus: how it fits in

Turn it on or off with `COMPANY_EVIDENCE_CONSENSUS` (`yahoo` by default). `none`, blank, `off`, `false`, `0`, `no` and `disabled` all turn it off; any other value fails at startup. An adapter passed explicitly to `collect_company_evidence(consensus_adapter=...)` always wins.

The adapter returns one `consensus` document per company:
- facts: `kind='consensus'`, `accounting_basis='analyst_consensus'`, `fiscal_period='next_report:<earnings date>'`, USD;
- a scheduled `Earnings release` event, `confirmed=False`, from the calendar date;
- the gap `consensus_provider_unavailable` when Yahoo returns nothing usable. It never raises into the collector.

Rules that keep a fetch-time snapshot from distorting the evidence:
- **Identity:** `ingest_source` leaves the fetch times out of a consensus document's identity. An unchanged calendar adds no rows, no new facts and no new package revision (which would otherwise re-queue discovery every cycle). A changed calendar is a new document whose facts supersede the old ones.
- **Freshness:** consensus documents never decide `freshness_status` (`build_company_package`) or `freshness_at_read` (`agent_builders/company_evidence_payload.py`). Both age the newest primary disclosure, so stale filings still read as stale.
- **Document window:** only the newest consensus document enters a package, and the 24-document cap (`MAX_DOCUMENTS`) applies to primary documents, so repeated refreshes can't push SEC filings out. A package with only consensus is not built.
- **Surprise claims:** the evidence-thesis checker accepts a beat, miss or surprise claim only when the claim cites a consensus fact and an actual of the same metric and fiscal period (`_surprise_claimed_without_consensus`). The `consensus` expectation baseline already requires an exact comparison key. A forward `next_report:` estimate can't support a claim about a past quarter.

Known limits:
- the currency is always labelled USD, so foreign reporters (e.g. SK hynix) are mislabelled;
- with no earnings date, the period is `next_report:unknown`.
