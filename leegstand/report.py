"""Report: one Telegram message per run, official sources first.
Every entry carries the link to the Dutch original; facts are shown as
verbatim Dutch quotes, so the reader checks the primary wording, not our retelling."""
import html
import logging

import requests

from . import config

log = logging.getLogger(__name__)
TG_LIMIT = 4000   # Telegram hard limit is 4096, keep headroom for HTML entities

LABEL = {"official": "OFFICIAL", "news": "NEWS"}


def _e(s):
    return html.escape(s or "", quote=False)


def format_entry(row):
    f = row.get("facts") or {}
    head = "[%s] %s" % (LABEL.get(row["doc_type"], row["doc_type"].upper()),
                        row["published_at"] or "n/a")
    if f.get("municipality"):
        head += " | " + f["municipality"]
    flags = row.get("flags") or []
    if "summary_from_snippet_only" in flags:
        head += " (headline only)"          # news via Google News: no article text behind it
    lines = ["<b>%s</b>" % _e(head),
             "<i>%s</i>" % _e(row["title_nl"])]
    if row.get("summary_en"):
        lines.append(_e(row["summary_en"]))
    elif f.get("relevant") is None:
        lines.append("(LLM extraction failed, see original)")
    for key, label in (("rate_quote_nl", "Rate"), ("effective_quote_nl", "Start"),
                       ("vacancy_quote_nl", "Vacant if")):
        if f.get(key):
            lines.append("%s (NL): \"%s\"" % (label, _e(f[key])))
    warn = [x for x in flags if x.startswith("unverified")]   # real fact problems only
    if warn:
        lines.append("Check: " + _e(", ".join(warn)))
    lines.append('<a href="%s">Original (NL)%s</a>' % (
        html.escape(row["url"]), " - " + _e(row["publisher"]) if row.get("publisher") else ""))
    return "\n".join(lines)


def build_messages(rows):
    """Split entries into <=TG_LIMIT messages without cutting an entry."""
    header = "<b>Leegstandbelasting monitor</b> - %d new\n" % len(rows)
    msgs, cur = [], header
    for row in rows:
        block = "\n" + format_entry(row) + "\n"
        if len(cur) + len(block) > TG_LIMIT:
            msgs.append(cur)
            cur = ""
        cur += block
    if cur.strip():
        msgs.append(cur)
    return msgs


def send(messages):
    if not (config.TG_BOT_TOKEN and config.TG_CHAT_ID):
        raise RuntimeError("TG_BOT_TOKEN / TG_CHAT_ID not set")
    url = "https://api.telegram.org/bot%s/sendMessage" % config.TG_BOT_TOKEN
    for m in messages:
        r = requests.post(url, json={"chat_id": config.TG_CHAT_ID, "text": m,
                                     "parse_mode": "HTML",
                                     "disable_web_page_preview": True}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError("telegram %s: %s" % (r.status_code, r.text[:200]))
