"""Entry point.

  python -m leegstand.run --collect-only      # sources only, no LLM, no DB (smoke test 1)
  python -m leegstand.run --dry-run --limit 3 # + LLM via gateway, print report, no DB/Telegram
  python -m leegstand.run                     # full: collect -> dedup -> extract -> store -> report
  python -m leegstand.run --no-send           # full but keep the report in the DB queue
"""
import argparse
import json
import logging
import re
import sys

import requests

from . import config, extract, report, sources

log = logging.getLogger("leegstand")


def collect(session):
    items = []
    for name, fn in (("cvdr", sources.collect_cvdr),
                     ("official", sources.collect_official),
                     ("feeds", sources.collect_feeds),
                     ("news", sources.collect_news)):
        try:
            items += fn(session)
        except Exception as e:      # one dead source must not kill the run
            log.error("collector %s failed: %s", name, e)
    # Dedup by URL, then by (publisher, title): the same AD article arrives once via
    # the AD feed (real URL) and once via Google News (redirect URL). Collector order
    # puts the feed version first, so the Google News copy is the one dropped.
    # Title key drops digits: the same Hardinxveld programme arrived as
    # "Volkshuisvestingsprogramma ..." (CVDR) and "Volkshuisvestingsprogramma 2026 ..."
    # (Gemeenteblad) and was reported twice on 2026-10-06.
    seen, seen_titles, uniq = set(), set(), []
    for it in items:
        tkey = (it["publisher"].lower().split(".")[0],
                " ".join(re.sub(r"\d+", "", it["title_nl"].lower()).split()))
        if it["url_hash"] in seen or (it["title_nl"] and tkey in seen_titles):
            continue
        seen.add(it["url_hash"])
        seen_titles.add(tkey)
        uniq.append(it)
    # official first, then newest
    uniq.sort(key=lambda i: (i["doc_type"] != "official", -(i["published_at"].toordinal()
                                                            if i["published_at"] else 0)))
    return uniq


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--collect-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--limit", type=int, default=config.MAX_ITEMS_PER_RUN)
    ap.add_argument("--match", action="append", default=[],
                    help="keep only items whose title/publisher/url contains this (repeatable, case-insensitive)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    s = requests.Session()
    items = collect(s)
    if a.match:
        pats = [m.lower() for m in a.match]
        items = [i for i in items if any(
            p in (i["title_nl"] + " " + i["publisher"] + " " + i["url"]).lower() for p in pats)]
    log.info("collected %d unique items", len(items))

    if a.collect_only:
        for it in items:
            print("%-8s %s %-30s %s" % (it["doc_type"], it["published_at"],
                                         it["publisher"][:30], it["title_nl"][:90]))
        return 0

    if a.dry_run:
        rows = []
        for it in items[: a.limit]:
            sources.fetch_body(it, s)
            extract.extract(it, s)
            print(json.dumps({"url": it["url"], "facts": it["facts"], "flags": it["flags"]},
                             ensure_ascii=False, indent=2, default=str))
            rows.append(it)
        print("\n----- TELEGRAM PREVIEW -----")
        for m in report.build_messages([r for r in rows if r["facts"].get("relevant") is not False]):
            print(m)
        return 0

    from . import store            # import late: --collect-only / --dry-run need no psycopg2
    conn = store.connect()
    known = store.known_hashes(conn, [x["url_hash"] for x in items])
    new = [i for i in items if i["url_hash"] not in known]
    log.info("%d new after dedup", len(new))
    skipped = 0
    for it in new[: a.limit]:
        sources.fetch_body(it, s)
        extract.extract(it, s)
        if any(f.startswith("llm_error") for f in it["flags"]):
            # Not stored: dedup would otherwise hide it forever. It stays "new"
            # and is picked up by the next run (gateway quota, timeout, bad JSON).
            skipped += 1
            continue
        store.insert(conn, it)
    if skipped:
        log.warning("%d items not stored (LLM failed), will retry next run", skipped)

    rows = store.unreported(conn)
    if rows and not a.no_send:
        report.send(report.build_messages(rows))
        store.mark_reported(conn, [r["id"] for r in rows])
        log.info("reported %d entries", len(rows))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
