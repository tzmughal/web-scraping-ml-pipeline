"""Clean / preprocess the raw scraped rows into ML-ready data.

Steps per row:
  1. Strip whitespace; title and URL are required.
  2. Price: "£51.77" -> 51.77 (float). Must be > 0.
  3. Rating: "Three" -> 3 (int, 1-5). Missing/unrecognised -> reject.
  4. Availability: "In stock" -> in_stock = 1, anything else -> 0,
     keeping the raw text in availability.
  5. Category: stripped; empty category -> reject (a row without a
     category is not usable as a labelled training example).
  6. Deduplicate on URL (keep first occurrence).

Multi-threading: rows are cleaned in parallel with a
ThreadPoolExecutor (max_workers=4) — cleaning is CPU-light and
independent per row, so threads are the right tool here. Async is
NOT used in cleaning; async lives in the Playwright scraper.
Deduplication and the final sort run single-threaded afterwards,
because they need a global view of all rows.

Outputs (written by clean_file):
  - data/books_clean.csv      cleaned, validated rows
  - data/books_rejected.csv   rejected rows, with a rejection reason
"""

import csv
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RATING_MAP = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
PRICE_RE = re.compile(r"[\d.]+")

CLEAN_FIELDS = ["title", "price", "rating", "availability", "in_stock",
                "category", "url", "source_page"]


def clean_row(row: dict) -> tuple[dict | None, str | None]:
    """Clean one raw row. Returns (cleaned_row, None) or (None, reason)."""
    title = (row.get("title") or "").strip()
    url = (row.get("url") or "").strip()
    if not title:
        return None, "missing_title"
    if not url.startswith("http"):
        return None, "missing_or_invalid_url"

    price_match = PRICE_RE.search(row.get("price_raw") or "")
    if not price_match:
        return None, "unparseable_price"
    try:
        price = float(price_match.group(0))
    except ValueError:
        return None, "unparseable_price"
    if price <= 0:
        return None, "non_positive_price"

    rating = RATING_MAP.get((row.get("rating_raw") or "").strip())
    if rating is None:
        return None, "missing_or_unknown_rating"

    category = (row.get("category") or "").strip()
    if not category:
        return None, "missing_category"

    availability = " ".join((row.get("availability_raw") or "").split())
    try:
        source_page = int(row.get("source_page") or 0)
    except ValueError:
        source_page = 0

    return {
        "title": title,
        "price": price,
        "rating": rating,
        "availability": availability,
        "in_stock": 1 if "In stock" in availability else 0,
        "category": category,
        "url": url,
        "source_page": source_page,
    }, None


def clean_rows(raw_rows: list[dict], max_workers: int = 4) -> dict:
    """Clean rows in parallel (threads). Returns cleaned, rejected, stats."""
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(clean_row, raw_rows))

    cleaned, rejected = [], []
    seen_urls: set[str] = set()
    duplicates = 0
    for raw, (row, reason) in zip(raw_rows, results):
        if row is None:
            rejected.append({**raw, "rejection_reason": reason})
            continue
        if row["url"] in seen_urls:
            duplicates += 1
            rejected.append({**raw, "rejection_reason": "duplicate_url"})
            continue
        seen_urls.add(row["url"])
        cleaned.append(row)
    return {"cleaned": cleaned, "rejected": rejected,
            "duplicates_removed": duplicates}


def clean_file(raw_path: str | Path, clean_path: str | Path,
               rejected_path: str | Path | None = None) -> dict:
    """Read a raw CSV, clean it (threaded), write clean + rejected CSVs."""
    raw_path, clean_path = Path(raw_path), Path(clean_path)
    with raw_path.open(newline="", encoding="utf-8") as f:
        raw_rows = list(csv.DictReader(f))
    result = clean_rows(raw_rows)
    cleaned, rejected = result["cleaned"], result["rejected"]

    clean_path.parent.mkdir(parents=True, exist_ok=True)
    with clean_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CLEAN_FIELDS)
        writer.writeheader()
        writer.writerows(cleaned)
    if rejected_path:
        # Always rewrite the file (even when empty) so a stale file
        # from an earlier run can never be mistaken for this run.
        rejected_path = Path(rejected_path)
        with rejected_path.open("w", newline="", encoding="utf-8") as f:
            fields = list(rejected[0].keys()) if rejected else [
                "title", "price_raw", "rating_raw", "availability_raw",
                "category", "url", "source_page", "scraped_at",
                "rejection_reason"]
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rejected)
    result["raw_rows"] = len(raw_rows)
    result["cleaned_rows"] = len(cleaned)
    result["rejected_rows"] = len(rejected)
    return result


if __name__ == "__main__":
    import sys
    raw = sys.argv[1] if len(sys.argv) > 1 else "data/books_raw.csv"
    stats = clean_file(raw, "data/books_clean.csv", "data/books_rejected.csv")
    print(f"Cleaned {stats['cleaned_rows']} rows, rejected {stats['rejected_rows']} "
          f"({stats['duplicates_removed']} duplicates) from {stats['raw_rows']} raw rows.")
