"""Fallback scraper: Selenium (synchronous).

Same target and same output schema as src/scrape_playwright.py, but
with Selenium WebDriver instead of Playwright. This is the fallback
for environments where Playwright is unavailable.

Selenium is synchronous by design: pages are fetched one at a time,
with an explicit wait for the book listings and a sleep between
requests. No async here — the async work lives in the Playwright
scraper and the ThreadPoolExecutor cleaning step (see clean.py).

Browser engine: Firefox, not Chrome. In this environment Chromium
cannot reach the internet at all (see scrape_playwright.py), so
Selenium uses the Playwright Firefox binary; Selenium Manager
downloads the matching geckodriver automatically. The browser is
set to accept insecure certificates because the VM's egress proxy
terminates TLS with its own certificate. On a normal machine,
swap Firefox for Chrome — the scraping logic is the same.

Usage:
    python -m src.scrape_selenium --pages 2 --out data/books_raw_selenium.csv
"""

import argparse
import csv
import glob
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BASE_URL = "https://books.toscrape.com/"
CATALOGUE_URL = BASE_URL + "catalogue/page-{n}.html"

RATING_WORDS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}


def _find_firefox_binary() -> str | None:
    if os.environ.get("FIREFOX_BINARY"):
        return os.environ["FIREFOX_BINARY"]
    candidates = glob.glob(
        os.path.expanduser("~/.cache/ms-playwright/firefox-*/firefox/firefox"))
    if candidates:
        return sorted(candidates)[-1]
    for d in ("/usr/bin", "/usr/local/bin"):
        p = os.path.join(d, "firefox")
        if os.path.exists(p):
            return p
    return None


def _rating_from_class(class_attr: str) -> str:
    for word in RATING_WORDS:
        if word in (class_attr or ""):
            return word
    return ""


def scrape(pages: int = 2, delay: float = 0.5) -> list[dict]:
    """Scrape `pages` catalogue pages with Selenium; returns raw rows."""
    options = Options()
    options.add_argument("--headless")
    options.accept_insecure_certs = True
    binary = _find_firefox_binary()
    if binary:
        options.binary_location = binary
    # Egress proxy from the environment, host/port only (no
    # credentials — the proxy accepts unauthenticated requests
    # from this VM, verified with curl). Firefox proxy prefs:
    _proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    if _proxy:
        from urllib.parse import urlparse
        _p = urlparse(_proxy)
        options.set_preference("network.proxy.type", 1)
        options.set_preference("network.proxy.http", _p.hostname)
        options.set_preference("network.proxy.http_port", _p.port or 3128)
        options.set_preference("network.proxy.ssl", _p.hostname)
        options.set_preference("network.proxy.ssl_port", _p.port or 3128)
        options.set_preference("network.proxy.no_proxies_on", "localhost, 127.0.0.1")
    driver = webdriver.Firefox(service=Service(), options=options)
    rows: list[dict] = []
    try:
        wait = WebDriverWait(driver, 20)
        for n in range(1, pages + 1):
            driver.get(CATALOGUE_URL.format(n=n))
            wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "article.product_pod")))
            time.sleep(delay)
            for pod in driver.find_elements(By.CSS_SELECTOR, "article.product_pod"):
                link = pod.find_element(By.CSS_SELECTOR, "h3 a")
                title = (link.get_attribute("title") or "").strip()
                href = link.get_attribute("href") or ""
                price = pod.find_element(By.CSS_SELECTOR, "p.price_color").text.strip()
                rating = _rating_from_class(
                    pod.find_element(By.CSS_SELECTOR, "p.star-rating")
                    .get_attribute("class"))
                availability = " ".join(
                    pod.find_element(By.CSS_SELECTOR, "p.instock.availability")
                    .text.split())
                rows.append({
                    "title": title,
                    "price_raw": price,
                    "rating_raw": rating,
                    "availability_raw": availability,
                    "category": "",
                    # hrefs are relative to the catalogue page, not the
                    # site root (see scrape_playwright.py).
                    "url": urljoin(CATALOGUE_URL.format(n=n), href),
                    "source_page": n,
                })
            # Category lives on the detail page; visit each book once.
            for row in [r for r in rows if r["source_page"] == n]:
                driver.get(row["url"])
                wait.until(EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "ul.breadcrumb")))
                time.sleep(0.2)
                crumbs = driver.find_elements(By.CSS_SELECTOR, "ul.breadcrumb li a")
                # Anchors are Home, Books, <category> — the category
                # is the LAST link, not crumbs[1] ("Books").
                if crumbs:
                    row["category"] = crumbs[-1].text.strip()
        return rows
    finally:
        driver.quit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape books.toscrape.com with Selenium.")
    parser.add_argument("--pages", type=int, default=2,
                        help="Catalogue pages to scrape (20 books per page). Default: 2.")
    parser.add_argument("--out", default="data/books_raw_selenium.csv",
                        help="Output CSV path. Default: data/books_raw_selenium.csv")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Seconds to wait after each page load. Default: 0.5")
    args = parser.parse_args()

    rows = scrape(pages=args.pages, delay=args.delay)
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
    print(f"Selenium: scraped {len(rows)} raw rows from {args.pages} pages -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
