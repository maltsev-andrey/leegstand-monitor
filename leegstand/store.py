"""Postgres persistence on shared-postgres. psycopg2 (same driver choice as MLflow)."""
import json

import psycopg2
import psycopg2.extras

from . import config


def connect():
    return psycopg2.connect(config.PG_DSN)


def known_hashes(conn, hashes):
    if not hashes:
        return set()
    with conn.cursor() as cur:
        cur.execute("SELECT url_hash FROM publications WHERE url_hash = ANY(%s)", (list(hashes),))
        return {r[0] for r in cur.fetchall()}


def insert(conn, item):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO publications
               (url, url_hash, source, doc_type, publisher, title_nl, published_at,
                text_nl, text_source, summary_en, facts, flags, relevant)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (url_hash) DO NOTHING""",
            (item["url"], item["url_hash"], item["source"], item["doc_type"],
             item["publisher"], item["title_nl"], item["published_at"],
             item["text_nl"], item["text_source"], item["summary_en"],
             json.dumps(item["facts"], ensure_ascii=False), item["flags"],
             item["facts"].get("relevant")))
    conn.commit()


def unreported(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT id, url, source, doc_type, publisher, title_nl, published_at,
                              summary_en, facts, flags
                       FROM publications
                       WHERE reported_at IS NULL AND relevant IS NOT FALSE
                       ORDER BY (doc_type = 'official') DESC, published_at DESC NULLS LAST""")
        return cur.fetchall()


def mark_reported(conn, ids):
    if not ids:
        return
    with conn.cursor() as cur:
        cur.execute("UPDATE publications SET reported_at = now() WHERE id = ANY(%s)", (list(ids),))
    conn.commit()
