"""Offline tests: no network, no DB, no GPU. Run: python -m pytest -q tests/"""
import datetime as dt
import json

from leegstand import extract, report, sources

SRU_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<sru:searchRetrieveResponse xmlns:sru="http://docs.oasis-open.org/ns/search-ws/sruResponse"
  xmlns:gzd="http://standaarden.overheid.nl/sru" xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:overheidwetgeving="http://standaarden.overheid.nl/wetgeving/">
 <sru:numberOfRecords>1</sru:numberOfRecords>
 <sru:records><sru:record><sru:recordData><gzd:gzd>
  <gzd:originalData><overheidwetgeving:meta><overheidwetgeving:owmskern>
   <dcterms:identifier>gmb-2026-123456</dcterms:identifier>
   <dcterms:title>Verordening leegstandbelasting 2027</dcterms:title>
   <dcterms:creator>Almere</dcterms:creator>
   <dcterms:modified>2026-09-30</dcterms:modified>
  </overheidwetgeving:owmskern><overheidwetgeving:owmsmantel>
   <dcterms:available>2026-10-01</dcterms:available>
  </overheidwetgeving:owmsmantel></overheidwetgeving:meta></gzd:originalData>
  <gzd:enrichedData>
   <gzd:itemUrl manifestation="xml">https://repository.overheid.nl/frbr/officielepublicaties/gmb/2026/gmb-2026-123456/1/xml/gmb-2026-123456.xml</gzd:itemUrl>
   <gzd:itemUrl manifestation="html">https://repository.overheid.nl/frbr/officielepublicaties/gmb/2026/gmb-2026-123456/1/html/gmb-2026-123456.html</gzd:itemUrl>
   <gzd:preferredUrl>https://zoek.officielebekendmakingen.nl/gmb-2026-123456.html</gzd:preferredUrl>
  </gzd:enrichedData>
 </gzd:gzd></sru:recordData></sru:record></sru:records>
</sru:searchRetrieveResponse>"""

RSS_XML = b"""<?xml version="1.0"?><rss><channel>
<item><title>Almere voert leegstandbelasting in - Omroep Flevoland</title>
<link>https://news.google.com/rss/articles/ABC</link>
<pubDate>Mon, 05 Oct 2026 08:00:00 GMT</pubDate>
<description>&lt;a href="x"&gt;Almere voert leegstandbelasting in&lt;/a&gt;</description>
<source url="https://www.omroepflevoland.nl">Omroep Flevoland</source></item>
</channel></rss>"""

SOURCE_TEXT = ("Artikel 3. Het tarief van de leegstandbelasting bedraagt 2% van de WOZ-waarde. "
               "Een woning wordt als leegstaand aangemerkt indien gedurende een aaneengesloten "
               "periode van twaalf maanden niemand in de basisregistratie personen staat "
               "ingeschreven. De verordening treedt in werking op 1 januari 2027.")


def test_parse_sru_namespace_agnostic():
    items = sources.parse_sru(SRU_XML)
    assert len(items) == 1
    it = items[0]
    assert it["doc_type"] == "official"
    assert it["url"] == "https://zoek.officielebekendmakingen.nl/gmb-2026-123456.html"
    assert it["publisher"] == "Almere"
    assert it["published_at"] == dt.date(2026, 10, 1)


CVDR_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<searchRetrieveResponse xmlns="http://docs.oasis-open.org/ns/search-ws/sruResponse"
  xmlns:dcterms="http://purl.org/dc/terms/" xmlns:gzd="http://standaarden.overheid.nl/sru"
  xmlns:cvdr="http://standaarden.overheid.nl/cvdr/terms/">
<numberOfRecords>2</numberOfRecords><records>
<record><recordData><gzd:gzd><gzd:originalData><cvdr:meta><cvdr:owmskern>
  <dcterms:identifier>CVDR760001_1</dcterms:identifier>
  <dcterms:title>Verordening leegstandbelasting Almere 2027</dcterms:title>
  <dcterms:creator>Almere</dcterms:creator></cvdr:owmskern><cvdr:owmsmantel>
  <cvdr:inwerkingtredingDatum>2026-07-01</cvdr:inwerkingtredingDatum>
</cvdr:owmsmantel></cvdr:meta></gzd:originalData><gzd:enrichedData>
  <gzd:preferredUrl>https://lokaleregelgeving.overheid.nl/CVDR760001/1</gzd:preferredUrl>
</gzd:enrichedData></gzd:gzd></recordData></record>
<record><recordData><gzd:gzd><gzd:originalData><cvdr:meta><cvdr:owmskern>
  <dcterms:identifier>CVDR700002_1</dcterms:identifier>
  <dcterms:title>Verordening leegstandbelasting 2025 (ingetrokken)</dcterms:title>
  <dcterms:creator>Almere</dcterms:creator></cvdr:owmskern><cvdr:owmsmantel>
  <cvdr:inwerkingtredingDatum>2025-01-01</cvdr:inwerkingtredingDatum>
  <cvdr:uitwerkingtredingDatum>2026-06-30</cvdr:uitwerkingtredingDatum>
</cvdr:owmsmantel></cvdr:meta></gzd:originalData></gzd:gzd></recordData></record>
</records></searchRetrieveResponse>"""


def test_parse_cvdr_keeps_in_force_drops_repealed():
    items = sources.parse_sru(CVDR_XML, source="cvdr")
    assert len(items) == 1
    it = items[0]
    assert it["source"] == "cvdr" and it["doc_type"] == "official"
    assert it["url"] == "https://lokaleregelgeving.overheid.nl/CVDR760001/1"
    assert it["published_at"] == dt.date(2026, 7, 1)


def test_cvdr_url_fallback_from_identifier():
    xml = CVDR_XML.replace(
        b"<gzd:preferredUrl>https://lokaleregelgeving.overheid.nl/CVDR760001/1</gzd:preferredUrl>", b"")
    assert sources.parse_sru(xml, source="cvdr")[0]["url"] == "https://lokaleregelgeving.overheid.nl/CVDR760001"


def test_blocked_publishers():
    assert sources.is_blocked_publisher("HBVL")
    assert sources.is_blocked_publisher("KW.be")
    assert sources.is_blocked_publisher("Het Laatste Nieuws")
    assert not sources.is_blocked_publisher("AD.nl")
    assert not sources.is_blocked_publisher("1Almere")
    assert not sources.is_blocked_publisher("")


def test_parse_rss_strips_publisher_suffix():
    it = sources.parse_rss(RSS_XML)[0]
    assert it["title_nl"] == "Almere voert leegstandbelasting in"
    assert it["doc_type"] == "news" and it["publisher"] == "Omroep Flevoland"


def test_validate_keeps_verbatim_drops_fabricated():
    facts = {
        "relevant": True, "municipality": "Almere", "status": "adopted",
        "rate_quote_nl": "Het tarief van de leegstandbelasting bedraagt 2% van de WOZ-waarde.",
        "effective_quote_nl": "treedt in werking op 1 januari 2028",          # wrong year
        "vacancy_quote_nl": "periode van twaalf maanden",
        "exemption_quotes_nl": ["eigenaren die in het buitenland wonen"],      # invented
        "summary_en": "Almere levies 2% of the assessed value (WOZ-waarde); some reports say EUR 16.000.",
    }
    clean, flags = extract.validate(facts, SOURCE_TEXT + " Almere")
    assert clean["rate_quote_nl"].startswith("Het tarief")
    assert clean["effective_quote_nl"] is None
    assert clean["exemption_quotes_nl"] == []
    assert "dropped_effective_quote_nl_not_in_source" in flags
    assert "dropped_exemption_not_in_source" in flags
    assert any(f.startswith("unverified_amount_in_summary") and "16" in f for f in flags)
    assert not any("2%" in f for f in flags)


def test_validate_rejects_unknown_status_and_foreign_municipality():
    clean, flags = extract.validate({"status": "law", "municipality": "Utrecht",
                                     "summary_en": "x"}, SOURCE_TEXT)
    assert clean["status"] == "unknown"
    assert clean["municipality"] is None and "municipality_not_in_source" in flags


def test_focus_text_finds_mention_deep_in_document():
    filler = "Woonvisie hoofdstuk over de woningvoorraad. " * 400       # ~18k chars
    deep = "De gemeente onderzoekt de invoering van een leegstandsbelasting in 2027."
    doc = filler + deep + filler
    out = extract.focus_text(doc, budget=6000)
    assert len(out) <= 6000 + 20
    assert "leegstandsbelasting in 2027" in out
    assert out.startswith("Woonvisie")                 # head kept for document name
    assert extract.focus_text("short text", budget=6000) == "short text"
    assert extract.focus_text(filler, budget=500) == filler[:500]   # no hit -> head


def test_snippet_status_is_forced_unknown(monkeypatch):
    llm_out = json.dumps({"relevant": True, "municipality": "Almere", "status": "in_force",
                          "rate_quote_nl": None, "effective_quote_nl": None,
                          "vacancy_quote_nl": None, "exemption_quotes_nl": [],
                          "summary_en": "A fine of 2000 euros in Almere."})
    monkeypatch.setattr(extract, "call_llm", lambda *a, **k: llm_out)
    it = sources.parse_rss(RSS_XML)[0]
    it["text_nl"] = it["title_nl"] + " 2000 euro"
    it["text_source"] = "snippet"
    extract.extract(it)
    assert it["facts"]["status"] == "unknown"
    assert "status_reset_snippet_only" in it["flags"]


FEED_XML = b"""<?xml version="1.0"?><rss><channel>
<item><title>Almere voert leegstandsbelasting in</title><link>https://www.ad.nl/almere/x~a1/</link>
<pubDate>Mon, 05 Oct 2026 08:00:00 GMT</pubDate><description>Raad stemt in.</description></item>
<item><title>Files op de A2</title><link>https://www.ad.nl/verkeer/y~b2/</link>
<description>Lange files.</description></item>
</channel></rss>"""

ATOM_XML = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Leegstandsheffing: modelverordening gereed</title>
<link href="https://vng.nl/nieuws/model"/><updated>2026-10-01T10:00:00Z</updated>
<summary>De VNG publiceert de modelverordening.</summary></entry></feed>"""


def test_feed_filters_by_term_and_sets_publisher():
    items = sources.parse_rss(FEED_XML, source="feed", publisher_default="AD.nl", require_term=True)
    assert len(items) == 1
    assert items[0]["publisher"] == "AD.nl" and items[0]["url"].startswith("https://www.ad.nl/almere")
    assert items[0]["published_at"] == dt.date(2026, 10, 5)


def test_atom_feed_parsed():
    items = sources.parse_rss(ATOM_XML, source="feed", publisher_default="VNG", require_term=True)
    assert len(items) == 1 and items[0]["url"] == "https://vng.nl/nieuws/model"
    assert items[0]["published_at"] == dt.date(2026, 10, 1)


def test_relevant_override_and_year_check():
    facts = {"relevant": False, "municipality": "Zwijndrecht", "status": "discussion",
             "summary_en": "Zwijndrecht is considering a vacancy tax; a motion passed in 2025, start 2028."}
    clean, flags = extract.validate(facts, "Zwijndrecht motie 8 juli 2025 leegstandsbelasting onderzoeken")
    assert clean["relevant"] is True and "relevant_overridden_by_status" in flags
    assert "unverified_year_in_summary:2028" in flags
    assert not any("2025" in f for f in flags)


def test_parse_json_tolerates_fences():
    assert extract.parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_report_split_and_escape():
    row = {"doc_type": "official", "published_at": dt.date(2026, 10, 1), "title_nl": "A <b> & B",
           "summary_en": "s" * 900, "url": "https://x/?a=1&b=2", "publisher": "Almere",
           "facts": {"municipality": "Almere", "rate_quote_nl": "2% van de WOZ-waarde"},
           "flags": ["summary_from_snippet_only"]}
    msgs = report.build_messages([row] * 8)
    assert len(msgs) >= 2 and all(len(m) <= report.TG_LIMIT for m in msgs)
    assert "A &lt;b&gt; &amp; B" in msgs[0] and "&amp;b=2" in msgs[0]


def test_extract_end_to_end_with_mocked_gateway(monkeypatch):
    llm_out = json.dumps({
        "relevant": True, "municipality": "Almere", "status": "adopted",
        "rate_quote_nl": "bedraagt 2% van de WOZ-waarde",
        "effective_quote_nl": "treedt in werking op 1 januari 2027",
        "vacancy_quote_nl": None, "exemption_quotes_nl": [],
        "summary_en": "Almere adopted a vacancy tax of 2% of the assessed value (WOZ-waarde) from 1 January 2027."})
    monkeypatch.setattr(extract, "call_llm", lambda *a, **k: llm_out)
    it = sources.parse_sru(SRU_XML)[0]
    it["text_nl"], it["text_source"] = SOURCE_TEXT + " Almere", "page"
    extract.extract(it)
    assert it["flags"] == []
    assert it["facts"]["effective_quote_nl"] == "treedt in werking op 1 januari 2027"
    assert "2%" in report.format_entry(it)
