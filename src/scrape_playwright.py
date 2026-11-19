"""Primary scraper: async Playwright.

Scrapes books.toscrape.com, a public sandbox site built for scraping
practice. Collects title, price, rating, availability, category and URL.

Async where it matters:
  - Page fetches are async (asyncio + Playwright's async API).
  - Catalogue pages are fetched concurrently with asyncio.gather,
    limited by a semaphore (MAX_CONCURRENT_PAGES = 3).
  - Book detail pages (needed for the category, which is only shown
    on the detail page breadcrumb) are fetched by 5 concurrent
    workers (MAX_CONCURRENT_DETAILS = 5), each reusing one page.

Browser engine: Firefox, not Chromium. In this environment Chromium
cannot reach the internet at all — every Chromium navigation fails
with net::ERR_TUNNEL_CONNECTION_FAILED / ERR_BLOCKED_BY_CLIENT
against the VM's egress proxy, while Firefox through the same proxy
works. The context also sets ignore_https_errors=True because the
egress proxy terminates TLS with its own certificate, which the
browser does not trust (SEC_ERROR_UNKNOWN_ISSUER without it).
On a normal machine, swap `pw.firefox` for `pw.chromium` — the
rest of the code is engine-agnostic.

Politeness: small page limit (default 5 catalogue pages = 100 books),
a short sleep between requests, one browser, default user agent.
books.toscrape.com has no robots.txt (checked 20 Nov 2026, 404) and
is explicitly a sandbox for scraping practice; we still keep the
request rate low and never hit the site in a loop.

Usage:
    python -m src.scrape_playwright --pages 5 --out data/books_raw.csv
    python -m src.scrape_playwright --pages 2 --out data/books_raw.csv --delay 0.5
"""

import argparse
import asyncio
import csv
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright

BASE_URL = "https://books.toscrape.com/"
CATALOGUE_URL = BASE_URL + "catalogue/page-{n}.html"

RATING_WORDS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
MAX_CONCURRENT_PAGES = 3
MAX_CONCURRENT_DETAILS = 5


def _rating_from_class(class_attr: str) -> str:
    """'star-rating Three' -> 'Three' (cleaning to int happens in clean.py)."""
    for word in RATING_WORDS:
        if word in (class_attr or ""):
            return word
    return ""


async def _scrape_catalogue_page(context, page_no: int, sem: asyncio.Semaphore,
                                 delay: float) -> list[dict]:
    """Fetch one catalogue page and return its raw book listings."""
    async with sem:
        page = await context.new_page()
        try:
            await page.goto(CATALOGUE_URL.format(n=page_no),
                            wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(delay)
            pods = await page.query_selector_all("article.product_pod")
            rows = []
            for pod in pods:
                link = await pod.query_selector("h3 a")
                title = await link.get_attribute("title") if link else ""
                href = await link.get_attribute("href") if link else ""
                price_el = await pod.query_selector("p.price_color")
                price = (await price_el.inner_text()).strip() if price_el else ""
                rating_el = await pod.query_selector("p.star-rating")
                rating = _rating_from_class(
                    await rating_el.get_attribute("class") if rating_el else "")
                avail_el = await pod.query_selector("p.instock.availability")
                availability = (await avail_el.inner_text()).strip() if avail_el else ""
                rows.append({
                    "title": (title or "").strip(),
                    "price_raw": price,
                    "rating_raw": rating,
                    "availability_raw": " ".join(availability.split()),
                    "category": "",  # filled in from the detail page
                    # NOTE: hrefs on catalogue pages are relative to
                    # the catalogue page, not the site root, so resolve
                    # against the catalogue URL — urljoin(BASE_URL, href)
                    # produces a 404 (missing /catalogue/).
                    "url": urljoin(CATALOGUE_URL.format(n=page_no), href) if href else "",
                    "source_page": page_no,
                })
            return rows
        finally:
            await page.close()


async def _fetch_categories_worker(context, rows: list[dict],
                                     delay: float) -> list[dict]:
    """Fetch categories for a chunk of books, reusing ONE page.

    Creating a new browser page per book (100 pages) is very slow
    through this VM's proxy, so each of the 5 concurrent workers
    reuses a single page and visits its books one after another.
    The category is server-rendered HTML, so wait_until="commit"
    is enough — no need to wait for full DOM load.
    """
    page = await context.new_page()
    try:
        for row in rows:
            if not row["url"]:
                continue
            try:
                await page.goto(row["url"], wait_until="commit", timeout=30000)
                await asyncio.sleep(delay)
                crumb = await page.query_selector(
                    "ul.breadcrumb li:nth-child(3) a")
                if crumb:
                    row["category"] = (await crumb.inner_text()).strip()
            except Exception:
                row["category"] = ""
        return rows
    finally:
        await page.close()


async def scrape(pages: int = 5, delay: float = 0.3) -> list[dict]:
    """Scrape `pages` catalogue pages; returns raw rows (one per book)."""
    async with async_playwright() as pw:
        # This VM reaches the internet through an egress proxy set in
        # the environment (HTTPS_PROXY); the browser does not pick that
        # up on its own, so pass it explicitly. Nothing is hard-coded.
        _proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
        launch_kwargs = {"headless": True}
        if _proxy:
            # Playwright needs credentials as separate fields, not
            # embedded in the server URL.
            from urllib.parse import urlparse
            _p = urlparse(_proxy)
            _server = f"{_p.scheme}://{_p.hostname}:{_p.port or 3128}"
            _proxy_cfg = {"server": _server}
            if _p.username:
                _proxy_cfg["username"] = _p.username
            if _p.password:
                _proxy_cfg["password"] = _p.password
            launch_kwargs["proxy"] = _proxy_cfg
        browser = await pw.firefox.launch(**launch_kwargs)
        context = await browser.new_context(ignore_https_errors=True)
        try:
            page_sem = asyncio.Semaphore(MAX_CONCURRENT_PAGES)
            listings = await asyncio.gather(*[
                _scrape_catalogue_page(context, n, page_sem, delay)
                for n in range(1, pages + 1)
            ])
            rows = [r for page_rows in listings for r in page_rows]
            # 5 workers, each reusing one page for its chunk of books.
            chunks = [rows[i::MAX_CONCURRENT_DETAILS]
                      for i in range(MAX_CONCURRENT_DETAILS)]
            done = await asyncio.gather(*[
                _fetch_categories_worker(context, chunk, delay)
                for chunk in chunks if chunk
            ])
            return [r for chunk in done for r in chunk]
        finally:
            await context.close()
            await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape books.toscrape.com with async Playwright.")
    parser.add_argument("--pages", type=int, default=5,
                        help="Catalogue pages to scrape (20 books per page). Default: 5.")
    parser.add_argument("--out", default="data/books_raw.csv",
                        help="Output CSV path. Default: data/books_raw.csv")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="Seconds to wait after each page load. Default: 0.3")
    args = parser.parse_args()

    rows = asyncio.run(scrape(pages=args.pages, delay=args.delay))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    scraped_at = datetime.now(timezone.utc).isoformat()
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "title", "price_raw", "rating_raw", "availability_raw",
            "category", "url", "source_page", "scraped_at"])
        writer.writeheader()
        for r in rows:
            writer.writerow({**r, "scraped_at": scraped_at})
    print(f"Playwright: scraped {len(rows)} raw rows from {args.pages} pages -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
