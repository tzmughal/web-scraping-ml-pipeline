"""End-to-end pipeline: scrape -> clean -> SQLite -> metrics -> chart.

Runs the async Playwright scraper (primary), cleans the rows with a
thread pool (see clean.py), writes the cleaned data to CSV and
SQLite (see db.py), and writes data/metrics.json plus a chart.

What runs here, and what does not:
  - RUN: Playwright scrape, threaded cleaning, SQLite load, metrics, chart.
  - NOT RUN: the Selenium scraper (separate fallback script,
    src/scrape_selenium.py — run it on its own), PostgreSQL and
    MongoDB loaders (optional code in db.py, no server available).

Usage:
    python -m src.pipeline --pages 5
    python -m src.pipeline --pages 5 --skip-scrape   # re-clean data/books_raw.csv
"""

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.clean import clean_file
from src.db import save_sqlite
from src.scrape_playwright import scrape

ROOT = Path(__file__).resolve().parent.parent


def make_chart(cleaned: list[dict], out_path: Path) -> None:
    """Horizontal bar chart: books per category."""
    if not cleaned:
        raise ValueError("No cleaned rows to chart — check data/books_rejected.csv for why rows were rejected.")
    counts = Counter(r["category"] for r in cleaned)
    categories, values = zip(*sorted(counts.items(), key=lambda kv: kv[1]))
    plt.figure(figsize=(10, 6))
    plt.barh(categories, values)
    plt.xlabel("Books")
    plt.ylabel("Category")
    plt.title("Books per category (cleaned dataset)")
    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_path, dpi=150)
    plt.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the scraping + cleaning pipeline.")
    parser.add_argument("--pages", type=int, default=5,
                        help="Catalogue pages to scrape. Default: 5 (100 books).")
    parser.add_argument("--skip-scrape", action="store_true",
                        help="Skip scraping; re-clean the existing data/books_raw.csv.")
    args = parser.parse_args()

    raw_path = ROOT / "data" / "books_raw.csv"
    if not args.skip_scrape:
        rows = asyncio.run(scrape(pages=args.pages, delay=0.3))
        import csv as _csv
        scraped_at = datetime.now(timezone.utc).isoformat()
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        with raw_path.open("w", newline="", encoding="utf-8") as f:
            writer = _csv.DictWriter(f, fieldnames=[
                "title", "price_raw", "rating_raw", "availability_raw",
                "category", "url", "source_page", "scraped_at"])
            writer.writeheader()
            for r in rows:
                writer.writerow({**r, "scraped_at": scraped_at})
        pages_scraped = args.pages
    else:
        pages_scraped = args.pages

    stats = clean_file(raw_path, ROOT / "data" / "books_clean.csv",
                       ROOT / "data" / "books_rejected.csv")
    import csv as _csv
    with (ROOT / "data" / "books_clean.csv").open(newline="", encoding="utf-8") as f:
        cleaned = list(_csv.DictReader(f))
    for r in cleaned:
        r["price"] = float(r["price"])
        r["rating"] = int(r["rating"])
        r["in_stock"] = int(r["in_stock"])
        r["source_page"] = int(r["source_page"])

    db_rows = save_sqlite(cleaned, ROOT / "data" / "books_clean.db")
    make_chart(cleaned, ROOT / "screenshots" / "books_per_category.png")

    prices = [r["price"] for r in cleaned]
    metrics = {
        "source": "books.toscrape.com (public scraping-practice sandbox)",
        "scraper": "Playwright async (primary)",
        "pages_scraped": pages_scraped,
        "raw_rows": stats["raw_rows"],
        "cleaned_rows": stats["cleaned_rows"],
        "rejected_rows": stats["rejected_rows"],
        "duplicate_rows_removed": stats["duplicates_removed"],
        "rows_in_sqlite": db_rows,
        "categories": dict(sorted(Counter(r["category"] for r in cleaned).items())),
        "category_count": len(set(r["category"] for r in cleaned)),
        "price_min": round(min(prices), 2) if prices else None,
        "price_max": round(max(prices), 2) if prices else None,
        "price_mean": round(sum(prices) / len(prices), 2) if prices else None,
        "rating_counts": {str(k): v for k, v in
                          sorted(Counter(r["rating"] for r in cleaned).items())},
        "in_stock_rows": sum(r["in_stock"] for r in cleaned),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    with (ROOT / "data" / "metrics.json").open("w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
