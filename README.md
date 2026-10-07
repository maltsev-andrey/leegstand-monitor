# leegstand-monitor

Daily monitor for the Dutch municipal vacancy tax (*leegstandbelasting*): collects
official publications and news in Dutch, extracts the facts with a small local LLM,
verifies every extracted fact against the source text in code, and sends an English
report to Telegram with links to the Dutch originals.

Built as a workstream of a self-hosted AI platform (RHEL, Ollama on a Tesla P100,
LiteLLM gateway, PostgreSQL). Runs unattended on a systemd timer.

```
timer 07:20 ─> collect (4 channels) ─> dedup ─> fetch text ─> LLM extract ─> validate ─> store ─> Telegram
```

## Why this exists

The tax became possible in March 2026, but every municipality sets its own rate and
start date in a local ordinance (*verordening*). News retellings distort the details
(a proposal in one city gets reported as "the Dutch vacancy tax is EUR 2000"). The only
reliable source is the primary publication, so the system is built around separating
primary sources from retellings and refusing to report numbers that are not in the text.

## Sources, in order of trust

| Channel | What | Index | doc_type |
|---|---|---|---|
| CVDR (zoekservice.overheid.nl, SRU) | consolidated municipal regulations, full-text index | `overheidrg.body` | official |
| Officiele bekendmakingen (repository.overheid.nl, SRU) | Gemeenteblad, Staatscourant, parliamentary papers (metadata index only) | `cql.textAndIndexes` | official |
| Publisher RSS feeds | NOS, NU.nl, AD, VNG: real article URLs, full text | term filter | news |
| Google News RSS (nl) | discovery across hundreds of outlets; links are JS redirects, headline only | term filter | news |

`doc_type` is assigned by the collector from the channel. The LLM never decides what is
a primary source.

## Fabrication is handled in code, not in the prompt

The model (qwen2.5-7b through a LiteLLM gateway) returns facts as **verbatim Dutch
quotes** plus a short English summary. `extract.validate()` then:

- drops every quote that is not a substring of the full source text (flag `dropped_*_not_in_source`);
- flags every euro amount, percentage and year in the English summary that does not
  occur in the source (`unverified_amount_in_summary`, `unverified_year_in_summary`);
- nulls a municipality name that is not in the source;
- forces `status = unknown` when the input was a headline only (a headline cannot say
  whether a council adopted anything; the 7b model guessed `in_force` for a proposal);
- sets `relevant = true` when the model named a tax stage but answered `relevant = false`
  for a long housing plan where the tax is one topic among many.

Long documents are not truncated at N characters: `focus_text()` sends windows around
every search-term hit, because a 60-page housing plan mentions the tax once on page 30.

Items whose LLM call failed (quota 429, timeout, bad JSON) are **not stored**, so URL
dedup cannot hide them; they are retried on the next run.

## Things learned the hard way

- The law says *leegstandbelasting*; every municipality writes *leegstandsbelasting*.
  Searching the "correct" spelling alone returns zero. Four spellings are searched.
- CVDR's `dcterms.title` index is whole-string exact match: useless for search. The
  full-text `overheidrg.body` index is the one that works.
- repository.overheid.nl indexes metadata only: an ordinance with the tax in article 14
  of a general tax regulation is invisible there.
- zoekservice.overheid.nl answers 406 to `+`-encoded spaces; build the CQL query string
  by hand with `%20` and send `Accept: application/xml`.
- *leegstandsheffing* is also a Belgian (Flemish) tax: two thirds of Google News hits
  were Belgian. A publisher blocklist before the LLM saves the GPU for real work.
- Google News article links are JS redirects with no text behind them; direct publisher
  feeds are the only route to full articles.

## Layout

```
leegstand/        config.py sources.py extract.py store.py report.py run.py
sql/schema.sql    publications table (verbatim Dutch text, facts jsonb, flags text[])
tests/            offline tests: parsers, validate(), focus_text(), report splitting
deploy/           systemd oneshot service + timer, env.example
deploy/ansible/   playbook that renders /etc/leegstand/env from an ansible-vault file
```

## Run

```bash
python3.12 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python -m pytest -q tests/
cp deploy/env.example /etc/leegstand/env      # fill in gateway key, DB DSN, Telegram
set -a; . /etc/leegstand/env; set +a
venv/bin/python -m leegstand.run --collect-only          # sources only
venv/bin/python -m leegstand.run --dry-run --match almere # + LLM, preview, no DB/Telegram
venv/bin/python -m leegstand.run                          # full run (what the timer does)
```

The LLM endpoint is any OpenAI-compatible `/v1/chat/completions` (LiteLLM, vLLM,
Ollama with the OpenAI shim). Cost on a P100 with a 7b model: ~9 GPU-seconds per document.

## Roadmap

- Web viewer with a per-municipality timeline (Flask).
- Russian summary as a second stage on top of the English one (needs a larger model).
- Tweede Kamer open data, VNG model ordinance watch.
- Kubernetes CronJob under GitOps.
