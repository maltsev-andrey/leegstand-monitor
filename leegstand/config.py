"""Runtime config. Everything comes from the env file (systemd EnvironmentFile),
same pattern as /opt/sparklab/env. No secrets in code or in git."""
import os

# --- LLM via LiteLLM gateway (Ch07 governance: own consumer key, quotas, audit) ---
LITELLM_URL = os.environ.get("LITELLM_URL", "http://GPU_HOST:30900")
LITELLM_KEY = os.environ.get("LITELLM_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen2.5-7b")   # gateway model_name, not ollama tag
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "300"))
LLM_RETRIES = int(os.environ.get("LLM_RETRIES", "3"))
LLM_BACKOFF = int(os.environ.get("LLM_BACKOFF", "20"))   # seconds between 429 retries

# --- Storage: shared-postgres on db-host ---
PG_DSN = os.environ.get(
    "PG_DSN", "postgresql://leegstand:CHANGE_ME@DB_HOST:5432/leegstand")

# --- Output: Telegram, same bot as the IT digest or a separate one ---
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

# --- Collection ---
# Both spellings are in active use: the Gemeentewet says "leegstandbelasting",
# rijksoverheid.nl and many councils write "leegstandsheffing".
SEARCH_TERMS = ["leegstandbelasting", "leegstandsbelasting", "leegstandsheffing",
                "leegstandheffing"]
SRU_URL = os.environ.get("SRU_URL", "https://repository.overheid.nl/sru")
# CVDR = consolidated local regulations (the actual municipal verordeningen).
# repository.overheid.nl matches metadata only (proven 2026-10-06: 0 verordeningen),
# zoekservice CVDR indexes full text.
CVDR_URL = os.environ.get("CVDR_URL", "https://zoekservice.overheid.nl/sru/Search")
# CQL index used for the term search in CVDR. Empty string = server default index
# (plain term, no "index = term"). Set after checking operation=explain on the server.
CVDR_INDEX = os.environ.get("CVDR_INDEX", "cql.textAndIndexes")
# "leegstandsheffing" is also the Flemish (Belgian) tax; Google News hl=nl returns
# Flemish outlets in bulk. Dropped by publisher before the LLM: no GPU-seconds on them.
BLOCKED_PUBLISHERS = {
    "hbvl", "hln", "nieuwsblad", "gva", "kw.be", "de internetgazet", "de standaard",
    "de morgen", "het laatste nieuws", "de tijd", "vrt", "vrt nws", "bruzz", "robtv",
    "tvoost", "tvl", "focus-wtv", "atv", "ring tv", "radio 2", "made in", "het belang van limburg",
    "gazet van antwerpen", "krant van west-vlaanderen",
}
GNEWS_URL = "https://news.google.com/rss/search"
# Direct publisher feeds: Google News links are JS redirects (no article text),
# these give real URLs so fetch_body() gets the full article. Feeds carry only
# the latest ~20-50 items, so this is forward monitoring; Google News stays for
# discovery/backfill. Items are kept only if a SEARCH_TERM appears in title/description.
# Verify each URL once with: curl -sI <url> | head -1  (a feed can move; a dead feed
# only logs a warning, it never kills the run).
NEWS_FEEDS = {                                   # verified 200 + xml on 2026-10-06
    "NOS": "https://feeds.nos.nl/nosnieuwsbinnenland",
    "NU.nl": "https://www.nu.nl/rss/Algemeen",
    "AD.nl": "https://www.ad.nl/rss.xml",
    "VNG": "https://vng.nl/rss.xml",
    "Stadszaken": "https://stadszaken.nl/rss",   # flaky (curl 000, python ok); watch the log
}
# Dropped 2026-10-06: RTL (403 bot block), Binnenlands Bestuur and Rijksoverheid (404, feed moved).
# Add/override without touching this file:
#   NEWS_FEEDS_EXTRA="Binnenlands Bestuur=https://...;Rijksoverheid VRO=https://..."
#   NEWS_FEEDS_DISABLE="Stadszaken,AD.nl"
for _pair in filter(None, os.environ.get("NEWS_FEEDS_EXTRA", "").split(";")):
    _name, _, _url = _pair.partition("=")
    if _name.strip() and _url.strip():
        NEWS_FEEDS[_name.strip()] = _url.strip()
for _name in filter(None, os.environ.get("NEWS_FEEDS_DISABLE", "").split(",")):
    NEWS_FEEDS.pop(_name.strip(), None)
HTTP_TIMEOUT = 30
USER_AGENT = "sparklab-leegstand-monitor/0.1 (+research; contact via github maltsev-andrey)"
MAX_SOURCE_CHARS = 6000          # text sent to the 7b model; keeps prefill cheap
MAX_ITEMS_PER_RUN = int(os.environ.get("MAX_ITEMS_PER_RUN", "25"))
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "45"))
