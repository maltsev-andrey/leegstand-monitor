"""Stage 1: Dutch source -> structured facts + English summary, one LLM pass.

Fabrication is handled architecturally (same lesson as jobhunter tailoring):
  * the model returns facts as VERBATIM Dutch quotes;
  * code checks every quote is a real substring of the source text;
  * every euro amount in the English summary must exist in the source;
  * anything that fails is dropped or flagged, never silently kept.
"""
import json
import logging
import re
import time

import requests

from . import config

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You analyse Dutch government and news texts about the municipal vacancy tax \
(leegstandbelasting / leegstandsheffing) on homes in the Netherlands.

Return ONLY one JSON object, no markdown, no commentary, with exactly these keys:
{
  "relevant": true/false,          // false if the text is not about a tax on vacant homes in NL (e.g. Belgian/Flemish rules, offices only, unrelated)
  "municipality": string or null,  // Dutch municipality name if the text is about one specific gemeente
  "status": "in_force" | "adopted" | "proposed" | "discussion" | "unknown",
  "rate_quote_nl": string or null, // EXACT copy of the Dutch sentence/phrase stating the tax amount or rate, else null
  "effective_quote_nl": string or null, // EXACT copy of the Dutch phrase stating when the tax starts, else null
  "vacancy_quote_nl": string or null,   // EXACT copy of the Dutch phrase defining when a home counts as vacant, else null
  "exemption_quotes_nl": [string],      // EXACT copies of Dutch phrases listing exemptions, [] if none
  "summary_en": string             // 3-4 sentences in English
}

Hard rules:
- relevant is true whenever the text is about a tax or levy on vacant HOMES in the Netherlands, \
also when the municipality is not named, the measure is only proposed, the text is a short headline, \
or the tax is only one topic in a longer housing document. \
It is false for Belgian/Flemish municipalities, for offices/shops only, or for unrelated housing policy.
- *_quote_nl fields are copied character-for-character from the input. Do not translate, shorten or paraphrase them. If unsure, use null.
- Never invent amounts, dates, percentages or municipality names. Only use what is in the text.
- status: use "in_force" or "adopted" ONLY if the text explicitly says the council adopted the \
verordening or the tax is being levied. A proposal, plan, debate or intention is "proposed" or "discussion". \
If the text is too short to tell, use "unknown".
- In summary_en keep amounts and dates exactly as in the source. Write plain English; put a Dutch word in \
brackets only for a legal term that has no exact English equivalent, e.g. "assessed value (WOZ-waarde)". \
Never copy whole Dutch sentences or the title into the summary.
- Do not add legal advice or your own opinion."""

EURO_RE = re.compile(r"(?:€|EUR|euro)\s?\d[\d.,]*|\d[\d.,]*\s?(?:€|euro|EUR)", re.I)
PCT_RE = re.compile(r"\d+(?:[.,]\d+)?\s?(?:%|procent)", re.I)
_WS = re.compile(r"\s+")


def _norm(s):
    return _WS.sub(" ", (s or "")).strip().lower()


def _digits(s):
    return re.sub(r"\D", "", s or "")


def focus_text(text, terms=None, budget=None, window=1200):
    """Pick the parts of a long document that mention the search terms.

    A 60-page woonplan mentions the tax once on page 30; sending the first 6000
    chars misses it (seen on Zwijndrecht, 2026-10-06). Collect +-window chars
    around every hit, merge overlapping windows, fall back to the head if there
    are no hits or the document is short anyway."""
    terms = terms or config.SEARCH_TERMS
    budget = budget or config.MAX_SOURCE_CHARS
    if len(text) <= budget:
        return text
    low = text.lower()
    spans = []
    for t in terms:
        start = 0
        while True:
            i = low.find(t.lower(), start)
            if i < 0:
                break
            spans.append((max(0, i - window), min(len(text), i + len(t) + window)))
            start = i + len(t)
    if not spans:
        return text[:budget]
    spans.sort()
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    # always include the head: it carries the document's own name and the municipality
    head = text[: min(800, merged[0][0])]
    parts = [head] if head else []
    used = len(head)
    for a, b in merged:
        chunk = text[a:b]
        if used + len(chunk) > budget:
            chunk = chunk[: max(0, budget - used)]
        if not chunk:
            break
        parts.append(chunk)
        used += len(chunk)
    return "\n[...]\n".join(parts)


def call_llm(text, title, session=None):
    s = session or requests.Session()
    user = "TITLE: %s\n\nTEXT:\n%s" % (title, focus_text(text))
    body = {
        "model": config.LLM_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": user}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "metadata": {"workload": "leegstand-monitor"},
    }
    url = config.LITELLM_URL.rstrip("/") + "/v1/chat/completions"
    hdrs = {"Authorization": "Bearer " + config.LITELLM_KEY}
    for attempt in range(config.LLM_RETRIES + 1):
        r = s.post(url, headers=hdrs, json=body, timeout=config.LLM_TIMEOUT)
        if r.status_code == 429 and attempt < config.LLM_RETRIES:
            # Ch07 key quota (rpm_limit) hit: a 7b answer takes ~10 s, so a batch of 10
            # lands inside one minute. Honour Retry-After if the gateway sends it.
            wait = int(r.headers.get("Retry-After", 0)) or config.LLM_BACKOFF
            log.info("429 from gateway, sleeping %ds (attempt %d)", wait, attempt + 1)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def parse_json(raw):
    raw = raw.strip()
    m = re.search(r"\{.*\}", raw, re.S)       # tolerate ```json fences from small models
    if not m:
        raise ValueError("no JSON object in model output")
    return json.loads(m.group(0))


def validate(facts, source_text):
    """Return (clean_facts, flags). Pure function, unit-tested."""
    flags = []
    src = _norm(source_text)
    src_digits_amounts = {_digits(a) for a in EURO_RE.findall(source_text)}
    src_digits_amounts |= {_digits(a) for a in PCT_RE.findall(source_text)}

    clean = {
        "relevant": bool(facts.get("relevant", False)),
        "municipality": facts.get("municipality") or None,
        "status": facts.get("status") if facts.get("status") in
        ("in_force", "adopted", "proposed", "discussion", "unknown") else "unknown",
        "summary_en": (facts.get("summary_en") or "").strip(),
    }

    for key in ("rate_quote_nl", "effective_quote_nl", "vacancy_quote_nl"):
        q = facts.get(key)
        if q and _norm(q) in src:
            clean[key] = q.strip()
        else:
            clean[key] = None
            if q:
                flags.append("dropped_%s_not_in_source" % key)

    ex = []
    for q in facts.get("exemption_quotes_nl") or []:
        if isinstance(q, str) and _norm(q) in src:
            ex.append(q.strip())
        elif q:
            flags.append("dropped_exemption_not_in_source")
    clean["exemption_quotes_nl"] = ex

    if clean["municipality"] and _norm(clean["municipality"]) not in src:
        flags.append("municipality_not_in_source")
        clean["municipality"] = None

    # Every euro amount / percentage the summary states must exist in the source.
    for amt in EURO_RE.findall(clean["summary_en"]) + PCT_RE.findall(clean["summary_en"]):
        if _digits(amt) and _digits(amt) not in src_digits_amounts:
            flags.append("unverified_amount_in_summary:%s" % amt.strip())

    # Years stated in the summary must appear in the source (amounts are checked above,
    # dates were not: a model can shift "2027" to "2028" as easily as 2000 to 16000).
    src_years = set(re.findall(r"\b(19\d{2}|20\d{2})\b", source_text))
    for y in set(re.findall(r"\b(19\d{2}|20\d{2})\b", clean["summary_en"])):
        if y not in src_years:
            flags.append("unverified_year_in_summary:%s" % y)

    # Internal consistency: a named tax stage means the text IS about the tax.
    # qwen2.5-7b returned relevant=false + status=discussion + municipality=Zwijndrecht
    # for a woonplan where the tax is one topic among many (2026-10-06).
    if clean["relevant"] is False and clean["status"] in ("in_force", "adopted", "proposed", "discussion"):
        clean["relevant"] = True
        flags.append("relevant_overridden_by_status")

    if not clean["summary_en"]:
        flags.append("empty_summary")
    return clean, sorted(set(flags))


def extract(item, session=None):
    """Fill item['facts'], item['summary_en'], item['flags']. Never raises."""
    try:
        raw = call_llm(item["text_nl"], item["title_nl"], session=session)
        facts = parse_json(raw)
        clean, flags = validate(facts, item["text_nl"] + " " + item["title_nl"])
    except Exception as e:  # one bad item must not kill the run
        log.warning("extract failed %s: %s", item["url"], e)
        clean, flags = {"relevant": None, "summary_en": ""}, ["llm_error:%s" % type(e).__name__]
    if item.get("text_source") in ("snippet", "title"):
        flags.append("summary_from_snippet_only")
        # A headline cannot tell whether a council adopted anything. The 7b model
        # answered "in_force" for a proposal (RTL/Almere, 2026-10-06); status from
        # a snippet is a guess by construction, so it is not allowed to be one.
        if clean.get("status") not in (None, "unknown"):
            flags.append("status_reset_snippet_only")
            clean["status"] = "unknown"
    item["facts"] = clean
    item["summary_en"] = clean.get("summary_en", "")
    item["flags"] = flags
    return item
