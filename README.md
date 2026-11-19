# Web Scraping and Data Pipeline for ML Training Data

**Repo:** `tzmughal/web-scraping-ml-pipeline` — https://github.com/tzmughal/web-scraping-ml-pipeline

A portfolio project by **Muhammad Talha Zahid** — BS Computer Science
(Allama Iqbal Open University, 2020–2024); Operations Analyst, Honda
Downtown Rawalpindi (Nov 2025–Aug 2026); freelance Data Scientist &
Analyst on Fiverr since Aug 2023 (180+ projects). Built to match an
AI/ML Engineer role: collect, scrape and preprocess data for ML
training, with async scraping, multi-threaded cleaning, and a
database layer for SQLite, PostgreSQL and MongoDB.

## The problem

ML models are only as good as their training data, and training data
rarely arrives clean. This project is the whole front half of that
job, end to end: scrape a live site, handle the mess a real site
gives you (relative URLs, text prices like `£51.77`, word ratings
like `Three`, missing fields), validate every row, deduplicate it,
and land it in a database in a schema a model can actually train on.

## The target site — and why it is legal to scrape

**https://books.toscrape.com** — a public sandbox site built
specifically for scraping practice. Its books, prices and categories
are sample data, not a real shop, and the site exists so people can
learn scraping on it. It has no `robots.txt` (checked 20 Nov 2026 —
the file returns 404), and the scraper still behaves politely: a
small page limit (5 catalogue pages), a short delay after each page
load, one browser, and concurrent requests capped at 5.

## Results — from the actual run, no invented numbers

Run: 19 Nov 2026. Primary scraper (Playwright, async) took
about 2 minutes 45 seconds for 5 pages.

| Metric | Value |
|---|---|
| Pages scraped | 5 catalogue pages |
| Raw rows collected | 100 |
| Cleaned rows | 100 |
| Rejected rows | 0 |
| Duplicate rows removed | 0 |
| Rows in SQLite (`data/books_clean.db`) | 100 |
| Categories | 29 |
| Price range | £10.16 – £58.11 |
| Mean price | £34.56 |
| In stock | 100 / 100 |
| Rating 1 / 2 / 3 / 4 / 5 | 22 / 19 / 22 / 18 / 19 |

Top categories: Sequential Art 14, Nonfiction 12, Default 9,
Poetry 7, Fiction 5, Food and Drink 5, Add a comment 5.
("Default" and "Add a comment" are genuine categories in the
site's sandbox data, not scraper errors — I verified them against
the live detail pages.)

![Books per category](screenshots/books_per_category.png)

### Cross-check: the Selenium fallback agrees

The Selenium scraper was also run for real, on 2 pages:
**40 raw rows, 40 with a category, 21 categories**, and the same
titles in the same order as the Playwright run's first 40 rows.
Two independent scrapers producing the same data is the check
that the Playwright numbers are not an artefact of one tool.

## How it works

```
books.toscrape.com
  -> src/scrape_playwright.py   (async Playwright, primary)
  -> data/books_raw.csv         (raw rows, as scraped)
  -> src/clean.py               (ThreadPoolExecutor, 4 workers)
  -> data/books_clean.csv       (validated rows)
  -> data/books_clean.db        (SQLite, via src/db.py)
  -> data/metrics.json          (real counts from this run)
  -> screenshots/books_per_category.png
```

### 1. Scrape — Playwright, async (`src/scrape_playwright.py`)

- `asyncio` + Playwright's async API. Catalogue pages are fetched
  concurrently (`asyncio.gather`, semaphore of 3); book detail
  pages are fetched by 5 concurrent workers, each reusing one
  browser page.
- Each book collects: title, price, rating, availability,
  category, URL. The category only exists on the detail page
  (breadcrumb), so the scraper visits each book's detail page —
  100 detail fetches for 100 books.
- One real bug, found and fixed during the run: book links on
  catalogue pages are relative to the *catalogue page*, not the
  site root. Resolving them against the site root produced
  404s and empty categories for all 100 rows. They are now
  resolved against the catalogue page URL, and the first run's
  failure is documented here rather than hidden.

### 2. Scrape — Selenium, synchronous fallback (`src/scrape_selenium.py`)

- Same target, same output schema, same fields. Selenium is
  synchronous by design: pages are fetched one at a time with
  explicit waits and a sleep between requests.
- A second real bug, found the same way: the category is the
  *last* link in the breadcrumb (Home, Books, category), not
  the second — the first version returned "Books" for all 40
  rows. Fixed and re-run; the corrected run is the one reported
  above.

### 3. Clean — multi-threaded (`src/clean.py`)

- Rows are cleaned in parallel with a `ThreadPoolExecutor`
  (4 workers). Cleaning is independent per row, so threads are
  the right tool here; async is *not* used in cleaning.
- Price `"£51.77"` → `51.77` (float), rating `"Three"` → `3`
  (int, 1–5), availability `"In stock"` → `in_stock = 1`.
- A row is rejected if the title or URL is missing, the price
  does not parse or is not positive, the rating is missing or
  unknown, or the category is empty. Duplicates are removed
  on URL (keep first). Rejected rows go to
  `data/books_rejected.csv` with a reason per row — this run
  had none, so that file is header-only.
- Deduplication runs single-threaded after cleaning, because
  it needs a global view of all rows.

### 4. Store — SQLite actually run, PostgreSQL / MongoDB optional (`src/db.py`)

- **SQLite: actually run.** Every pipeline run writes the
  cleaned rows to `data/books_clean.db` (100 rows in this run).
- **PostgreSQL and MongoDB: code provided, NOT run.** There
  was no PostgreSQL or MongoDB server in this environment, so
  `load_postgres()` and `load_mongo()` in `src/db.py` are
  loaders for the identical schema, clearly marked optional.
  They are not claimed as run anywhere in this README.

## Schema

| Column | Type | Notes |
|---|---|---|
| title | TEXT | required |
| price | REAL | GBP, parsed from `£xx.xx`, must be > 0 |
| rating | INTEGER | 1–5, parsed from One–Five |
| availability | TEXT | raw text, e.g. `In stock` |
| in_stock | INTEGER | 1 if in stock, else 0 |
| category | TEXT | from the detail-page breadcrumb, required |
| url | TEXT | primary key / unique, used for dedup |
| source_page | INTEGER | catalogue page the book came from |

## How to run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install firefox
```

Full pipeline (scrape 5 pages, clean, load SQLite, metrics, chart):

```bash
python -m src.pipeline --pages 5
```

Re-clean the existing raw file without re-scraping:

```bash
python -m src.pipeline --pages 5 --skip-scrape
```

Playwright scraper on its own:

```bash
python -m src.scrape_playwright --pages 5 --out data/books_raw.csv
```

Selenium fallback on its own (2 pages, 40 books):

```bash
python -m src.scrape_selenium --pages 2 --out data/books_raw_selenium.csv
```

Clean only:

```bash
python -m src.clean data/books_raw.csv
```

PostgreSQL / MongoDB (optional, needs a running server):

```python
from src.db import load_postgres, load_mongo
load_postgres(rows, "host=localhost dbname=books user=postgres password=secret")
load_mongo(rows, "mongodb://localhost:27017/")
```

## Where async and threading are used — exactly

| Technique | Where | What it does |
|---|---|---|
| `asyncio` + async Playwright | `src/scrape_playwright.py` | Concurrent catalogue-page and detail-page fetches |
| `ThreadPoolExecutor` (4 workers) | `src/clean.py` | Parallel per-row cleaning |
| Synchronous Selenium | `src/scrape_selenium.py` | One-at-a-time fallback scraping (no async) |
| Single-threaded | `src/clean.py` (dedup), `src/db.py` | Deduplication and database writes |

## Environment note — why Firefox, not Chromium

In the VM this was built in, Chromium cannot reach the internet
at all: every Chromium navigation fails with
`net::ERR_TUNNEL_CONNECTION_FAILED` / `ERR_BLOCKED_BY_CLIENT`
against the VM's egress proxy, while Firefox through the same
proxy works. The browser context also sets
`ignore_https_errors=True` because the egress proxy terminates
TLS with its own certificate. Both scrapers therefore use the
Playwright Firefox binary, with the reason documented in the
code. On a normal machine, swap `pw.firefox` for `pw.chromium`
(Playwright) and Firefox for Chrome (Selenium) — the scraping
logic is engine-agnostic.

## Limitations — read this before reusing the data

- **Only the first 5 pages (100 of 1,000 books on the site).**
  The limit is deliberate and stated in the code; raise
  `--pages` to scrape more.
- **The data is sample data.** Prices, ratings and categories
  are the sandbox site's invented catalogue, not real commerce.
  It is a clean, labelled dataset for practising ML — price
  prediction, rating classification, category prediction —
  not a market dataset.
- **All 100 books in this sample are in stock**, so
  `in_stock` has no variance here. A larger scrape would be
  needed before that column is useful as a feature.
- **No ML model is trained in this project.** It produces
  ML-ready training data; the model is the next step, not
  something claimed here.
- **The site can change.** If books.toscrape.com changes its
  markup, the selectors in the scrapers will need updating —
  that is normal for any scraper, and the two-scraper
  cross-check above is how a breakage would show up.
