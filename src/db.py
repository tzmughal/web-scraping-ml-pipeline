"""Database layer.

SQLite is the database actually used in this project: the pipeline
writes the cleaned rows to data/books_clean.db on every run, no
server needed.

PostgreSQL and MongoDB loaders are provided as code for the same
schema, because the job description lists them — but they are
OPTIONAL and were NOT run here, because no PostgreSQL or MongoDB
server was available in this environment. Do not claim they were
run. To run them, start a server, set the connection string / URI
(see the functions below) and call the loader; the table/collection
shape is identical to the SQLite one.

Schema (all three stores):
    title TEXT, price REAL, rating INTEGER, availability TEXT,
    in_stock INTEGER (0/1), category TEXT, url TEXT (unique),
    source_page INTEGER
"""

import sqlite3
from pathlib import Path

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS books (
    title TEXT NOT NULL,
    price REAL NOT NULL,
    rating INTEGER NOT NULL,
    availability TEXT,
    in_stock INTEGER NOT NULL,
    category TEXT NOT NULL,
    url TEXT PRIMARY KEY,
    source_page INTEGER
)
"""
INSERT_SQL = """
INSERT OR REPLACE INTO books
(title, price, rating, availability, in_stock, category, url, source_page)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""


def _rows_as_tuples(rows: list[dict]) -> list[tuple]:
    return [(r["title"], float(r["price"]), int(r["rating"]),
             r["availability"], int(r["in_stock"]), r["category"],
             r["url"], int(r["source_page"])) for r in rows]


def save_sqlite(rows: list[dict], db_path: str | Path) -> int:
    """Write cleaned rows to SQLite. Returns rows written. Actually run."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(TABLE_SQL)
        conn.executemany(INSERT_SQL, _rows_as_tuples(rows))
        count = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    return count


def load_postgres(rows: list[dict], conn_str: str) -> int:
    """OPTIONAL — NOT RUN in this project (no PostgreSQL server here).

    Example conn_str: "host=localhost dbname=books user=postgres password=secret"
    Requires: pip install psycopg2-binary
    """
    import psycopg2
    tuples = _rows_as_tuples(rows)
    with psycopg2.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS books (
                title TEXT NOT NULL, price REAL NOT NULL,
                rating INTEGER NOT NULL, availability TEXT,
                in_stock INTEGER NOT NULL, category TEXT NOT NULL,
                url TEXT PRIMARY KEY, source_page INTEGER)""")
            cur.executemany(
                """INSERT INTO books
                   (title, price, rating, availability, in_stock, category, url, source_page)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (url) DO UPDATE SET
                     title = EXCLUDED.title, price = EXCLUDED.price,
                     rating = EXCLUDED.rating, availability = EXCLUDED.availability,
                     in_stock = EXCLUDED.in_stock, category = EXCLUDED.category,
                     source_page = EXCLUDED.source_page""",
                tuples)
            cur.execute("SELECT COUNT(*) FROM books")
            return cur.fetchone()[0]


def load_mongo(rows: list[dict], uri: str = "mongodb://localhost:27017/",
               db_name: str = "books", collection: str = "books") -> int:
    """OPTIONAL — NOT RUN in this project (no MongoDB server here).

    Requires: pip install pymongo, and a MongoDB server at `uri`.
    Documents use the same fields as the SQLite table, plus _id = url.
    """
    from pymongo import MongoClient
    docs = [{**r, "_id": r["url"]} for r in rows]
    with MongoClient(uri) as client:
        coll = client[db_name][collection]
        for doc in docs:
            coll.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return coll.count_documents({})
