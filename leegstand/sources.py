"""Collectors. Each returns a list of Item dicts with a fixed shape.

doc_type is assigned HERE, by code, from the channel the item came from.
The LLM never decides whether something is a primary source.

  official  - Officiele bekendmakingen (Gemeenteblad, Staatscourant, Staatsblad,
              Kamerstukken) via the KOOP SRU API. Municipal verordeningen with the
              actual rate live here.
  news      - Google News RSS (nl). Retellings; useful as early signal only.
"""
import datetime as dt
import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from . import config

log = logging.getLogger(__name__)
HDRS = {"User-Agent": config.USER_AGENT}


def _local(tag):
    """'{ns}title' -> 'title'. KOOP changes namespaces between versions,
    so we match on local names only."""
    return tag.rsplit("}", 1)[-1]


def normalize_url(url):
    p = urlsplit(url.strip())
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), p.query, ""))


def url_hash(url):
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()


def make_item(**kw):
    item = {
        "url": kw["url"],
        "url_hash": url_hash(kw["url"]),
        "source": kw["source"],
        "doc_type": kw["doc_type"],
        "title_nl": kw.get("title_nl") or "",
        "published_at": kw.get("published_at"),
        "publisher": kw.get("publisher") or "",
        "snippet_nl": kw.get("snippet_nl") or "",
        "text_nl": "",
        "text_source": "",
    }
    return item


# ---------------------------------------------------------------- official (SRU)

def _parse_date(s):
    if not s:
        return None
    s = s.strip()[:10]
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def parse_sru(xml_bytes, source="officielebekendmakingen"):
    """Parse an SRU searchRetrieve response into items. Namespace-agnostic,
    works for both the officielepublicaties and the CVDR collections."""
    root = ET.fromstring(xml_bytes)
    items = []
    for rec in root.iter():
        if _local(rec.tag) != "record":
            continue
        f = {}
        for el in rec.iter():
            name = _local(el.tag)
            text = (el.text or "").strip()
            if not text:
                continue
            # first occurrence wins; KOOP repeats some fields in enrichedData
            if name in ("title", "identifier", "creator", "type", "available",
                        "modified", "issued", "preferredUrl", "itemUrl",
                        "publicatienaam", "inwerkingtredingDatum", "uitwerkingtredingDatum",
                        "publicatiedatum") and name not in f:
                f[name] = text
            if name == "itemUrl" and el.attrib.get("manifestation") == "html":
                f["itemUrl_html"] = text
        url = f.get("preferredUrl") or f.get("itemUrl_html") or f.get("itemUrl")
        ident = f.get("identifier", "")
        if not url and ident.startswith(("gmb-", "stcrt-", "stb-", "kst-")):
            url = "https://zoek.officielebekendmakingen.nl/%s.html" % ident
        if not url and ident.startswith("CVDR"):
            url = "https://lokaleregelgeving.overheid.nl/%s" % ident.split("_")[0]
        if not url:
            continue
        if f.get("uitwerkingtredingDatum"):          # CVDR: repealed/expired version
            continue
        items.append(make_item(
            url=url,
            source=source,
            doc_type="official",
            title_nl=f.get("title", ""),
            publisher=f.get("creator", ""),
            published_at=_parse_date(f.get("available") or f.get("issued")
                                     or f.get("publicatiedatum")
                                     or f.get("inwerkingtredingDatum") or f.get("modified")),
        ))
    return items


def collect_cvdr(session=None):
    """Municipal verordeningen with the actual rate, from the CVDR collection.
    Full-text 'keyword' index; no date filter on purpose: on the first runs this
    backfills every verordening already in force (dedup makes re-runs free)."""
    s = session or requests.Session()
    if config.CVDR_INDEX:
        terms = " OR ".join('%s="%s"' % (config.CVDR_INDEX, t) for t in config.SEARCH_TERMS)
    else:
        terms = " OR ".join('"%s"' % t for t in config.SEARCH_TERMS)
    cql = "(%s) sortBy inwerkingtredingDatum/sort.descending" % terms
    # zoekservice answers 406 to requests' default '+' space encoding and missing Accept;
    # build the query string by hand with %20 and ask for XML explicitly.
    url = ("%s?version=2.0&operation=searchRetrieve&x-connection=cvdr"
           "&maximumRecords=50&startRecord=1&query=%s" % (config.CVDR_URL, quote(cql, safe="")))
    hdrs = dict(HDRS, Accept="application/xml, text/xml;q=0.9, */*;q=0.1")
    r = s.get(url, headers=hdrs, timeout=config.HTTP_TIMEOUT)
    r.raise_for_status()
    items = parse_sru(r.content, source="cvdr")
    log.info("cvdr: %d records", len(items))
    return items


def collect_official(session=None):
    s = session or requests.Session()
    terms = " OR ".join('cql.textAndIndexes="%s"' % t for t in config.SEARCH_TERMS)
    since = (dt.date.today() - dt.timedelta(days=config.LOOKBACK_DAYS)).isoformat()
    query = "c.product-area==officielepublicaties AND (%s) AND dt.modified>=%s" % (terms, since)
    url = "%s?maximumRecords=50&startRecord=1&query=%s" % (config.SRU_URL, quote(query, safe=""))
    hdrs = dict(HDRS, Accept="application/xml, text/xml;q=0.9, */*;q=0.1")
    r = s.get(url, headers=hdrs, timeout=config.HTTP_TIMEOUT)
    r.raise_for_status()
    items = parse_sru(r.content)
    log.info("official: %d records", len(items))
    return items


# ---------------------------------------------------------------- news (RSS)

def mentions_term(text):
    low = (text or "").lower()
    return any(t in low for t in config.SEARCH_TERMS)


def parse_rss(xml_bytes, source="googlenews", publisher_default="", require_term=False):
    """RSS 2.0 and Atom. Google News sets <source>; direct feeds take publisher_default."""
    root = ET.fromstring(xml_bytes)
    items = []
    entries = list(root.iter("item")) or [e for e in root.iter() if _local(e.tag) == "entry"]
    for it in entries:
        def ft(name):
            for el in it.iter():
                if _local(el.tag) == name:
                    return (el.text or "").strip(), el
            return "", None
        title = ft("title")[0]
        link, link_el = ft("link")
        if not link and link_el is not None:          # Atom: <link href="..."/>
            link = link_el.attrib.get("href", "")
        if not link:
            continue
        if require_term and not mentions_term(title + " " + (ft("description")[0] or ft("summary")[0])):
            continue
        pub = None
        raw_date = ft("pubDate")[0] or ft("published")[0] or ft("updated")[0]
        if raw_date:
            try:
                pub = parsedate_to_datetime(raw_date).date()
            except (TypeError, ValueError):
                pub = _parse_date(raw_date)
        publisher = ft("source")[0] or publisher_default
        desc_html = ft("description")[0] or ft("summary")[0]
        snippet = BeautifulSoup(desc_html, "html.parser").get_text(" ", strip=True)
        # Google News appends " - Publisher" to titles
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(" - " + publisher)]
        items.append(make_item(url=link, source=source, doc_type="news", title_nl=title,
                               publisher=publisher, published_at=pub, snippet_nl=snippet))
    return items


def is_blocked_publisher(publisher):
    p = (publisher or "").strip().lower()
    return bool(p) and (p in config.BLOCKED_PUBLISHERS or p.endswith(".be"))


def collect_news(session=None):
    s = session or requests.Session()
    q = " OR ".join(config.SEARCH_TERMS) + " when:%dd" % config.LOOKBACK_DAYS
    params = {"q": q, "hl": "nl", "gl": "NL", "ceid": "NL:nl"}
    r = s.get(config.GNEWS_URL, params=params, headers=HDRS, timeout=config.HTTP_TIMEOUT)
    r.raise_for_status()
    raw = parse_rss(r.content)
    items = [i for i in raw if not is_blocked_publisher(i["publisher"])]
    log.info("news: %d items, %d dropped (Belgian/blocked publisher)", len(items), len(raw) - len(items))
    return items


def collect_feeds(session=None):
    """Direct publisher RSS: real article URLs, full text later in fetch_body()."""
    s = session or requests.Session()
    items = []
    for name, url in config.NEWS_FEEDS.items():
        try:
            r = s.get(url, headers=HDRS, timeout=config.HTTP_TIMEOUT)
            r.raise_for_status()
            got = parse_rss(r.content, source="feed", publisher_default=name, require_term=True)
            log.info("feed %s: %d matching", name, len(got))
            items += got
        except (requests.RequestException, ET.ParseError) as e:
            log.warning("feed %s failed: %s", name, e)
    return items


# ---------------------------------------------------------------- body text

_WS = re.compile(r"\s+")


def extract_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        t.decompose()
    main = soup.find("main") or soup.find("article") or soup.find(id="content") or soup.body or soup
    return _WS.sub(" ", main.get_text(" ", strip=True)).strip()


def fetch_body(item, session=None):
    """Fill text_nl. Official docs: full text. Google News: links are JS redirects,
    so phase 1 works on title+snippet and says so (text_source='snippet')."""
    s = session or requests.Session()
    if item["source"] == "googlenews":
        item["text_nl"] = (item["title_nl"] + ". " + item["snippet_nl"]).strip()
        item["text_source"] = "snippet"
        return item
    try:
        r = s.get(item["url"], headers=HDRS, timeout=config.HTTP_TIMEOUT)
        r.raise_for_status()
        item["text_nl"] = extract_text(r.text)
        item["text_source"] = "page"
    except requests.RequestException as e:
        log.warning("body fetch failed %s: %s", item["url"], e)
        item["text_nl"] = item["title_nl"]
        item["text_source"] = "title"
    return item
