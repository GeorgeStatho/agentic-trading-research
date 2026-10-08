Free, no behavior change
1. Stop re-running the news schema setup on every call. This is likely the biggest win. The market setup in bootstrap.py:142 skips its work when the stored hash matches, so most calls cost one lookup. The news setup in news_db_modules/bootstrap.py:90 has no such check. Every call:

runs the migrations twice;
runs the full 463-line news_schema.sql and the catalyst schema;
checks the columns of 12 tables.
It's called from about a dozen places, once per payload, per opportunist batch save and per strategist call (strategist.py:375, company_opportunist.py:309 and others). The fix is to add the same hash check (or simply run it once per process per DB path).

2. Precompile the project's Python in the Docker image. Dockerfile.worker sets PYTHONDONTWRITEBYTECODE=1, and the copied project code is never compiled. So every process start recompiles every project module it imports from source. That includes each scrape subprocess (see #3). Adding RUN python -m compileall -q /app after COPY . . fixes it. The env var only stops Python from writing .pyc files; it still reads them.

3. Stop starting a fresh Python process for every scrape. news_refresh.py:43 launches a new interpreter for every sector, industry and company. Each one re-imports yfinance, pandas, playwright and scrapy, which is often a second or more of CPU per launch. You can keep the crash isolation and drop the startup cost with multiprocessing.get_context("forkserver") plus set_forkserver_preload([...]). Each scrape still gets its own process, but the heavy modules are imported only once.

4. Block images, fonts and media in Playwright. playwright_runner.py:217 loads full pages, but the code only keeps page.content(). A context.route("**/*", ...) that aborts image, media and font requests cuts Chromium's work a lot. Leave stylesheets and scripts alone so the rendered HTML stays the same.

5. Pause dashboard polling while the tab is hidden. scriptStatus.tsx:21 polls every 5 seconds, and useRemoteResource.ts:72 every 60 seconds, even in a background tab. Skipping the request when document.hidden is true changes nothing for someone looking at the page.

Nearly free, small risk of edge-case differences
6. Use the lxml parser for BeautifulSoup. "html.parser" is used throughout Webscraping/. Switching to "lxml" is several times faster, and lxml is already installed because scrapy depends on it. On broken HTML the two parsers can build slightly different trees, so run the extractor tests before relying on it.

7. SQLite read pragmas. Adding PRAGMA mmap_size = 268435456 and PRAGMA temp_store = MEMORY to get_connection in db_common.py makes large reads cheaper (the API and payload builders do many). The gain is small next to #1–#4.

Not worth touching
The API healthcheck every 10 seconds only returns {"status": "ok"}.
Lowering WEBSCRAPING_PLAYWRIGHT_CONCURRENCY or adding Docker cpus: limits lowers peak CPU, but total work stays the same and cycles take longer.