# Policy Corpus Pipeline (Phase 3, Scenario 2)

`scripts/build_policy_corpus.py` turns the hand-curated Phase 3 corpus into a reproducible, status-aware corpus with automated recall checking. It sweeps the federal legislature, the federal regulatory layer (DOU), the state fiscal layer (CONFAZ) and the priority states (CE, SP, MG, RS), merges everything under LexML `urn:lex` keys, and scores the sweep against `docs/phase3_redata_policy_corpus.json` (the gold set).

Built 2026-09-23. Network access from the build environment was blocked for every `*.leg.br` / `gov.br` host, so **no connector has been run live from here**. Two connectors (Câmara, Senado) were verified against live responses fetched separately that day; the rest are documented below with their verification level. The first thing to do on the Mac is a live smoke test (section 3).

## 1. Layout

```text
scripts/build_policy_corpus.py          CLI: sweep | validate-corpus | recall | list-connectors
scripts/policy_corpus/
  models.py        Document / Relation dataclasses, Status + Layer + RelationType enums, urn:lex builder, dedupe
  terms.py         PT-BR term set and the acceptance rule (high / medium / low precision)
  fetch.py         requests session with disk cache, per-host throttle, retries, encoding chain, provenance
  linking.py       cross-source relations (same bill in two houses, MP->PL->Lei, curated links), endpoint canonicalisation
  recall.py        gold-set recall (urn family -> type/number/year -> url), discoverability filter
  validate.py      schema + lifecycle consistency for the curated JSON
  store.py         JSONL (canonical), Parquet (if pyarrow), SQLite + FTS5, manifest
  sources.json     municipalities (IBGE), SAPL hosts, norm/URN lookups, replaces pairs, host throttles
  connectors/      one module per source (see table)
scripts/tests/test_policy_corpus.py     54 offline tests; fixtures shaped like the live responses
docs/phase3_redata_policy_corpus.json   curated gold set (47 documents, status-aware schema 2026-09)
docs/phase3_redata_policy_corpus.md     generated view of the JSON
clean_data/sovereign_compute_nexus/phase3_policy_corpus/   outputs + raw_cache (gitignored via clean_data/)
```

## 2. Connectors and what was verified

| Connector | Layer | Verification | What it does | Known limits |
| --- | --- | --- | --- | --- |
| `camara` | federal_bill | **live** | `GET /api/v2/proposicoes?keywords=<term>&dataApresentacaoInicio=` per query term, follows `links[rel=next]`, then `/proposicoes/{id}` for `urlInteiroTeor` and `statusProposicao`. On HTTP 400 it degrades through request shapes (`full_window` → per-year windows → `start_only` → `no_dates` → `keywords_only`), filters dates client-side when they left the request, and records the API's own error text in `manifest.connectors[].notes`. | `keywords` hits indexation terms, not full text. `itens` caps at 100. The first live run (2026-09-24) got 400 for the `full_window` shape over 2024-01-01..2026-09-24; the accepted shape is reported per term so the API rule can be pinned down. |
| `senado` | federal_bill | **live** | `GET /dadosabertos/processo?termo=<term>&dataInicioApresentacao=` (JSON array). On HTTP 400 it degrades (`termo_window` → `termo + ano` per year → `termo_start_only` → `termo_only`) and logs the API message. `tipoNorma/numeroNorma/anoNorma` lookups resolve the process that generated a norm (Lei 15.504 -> PL 278/2026) and emit `enacted_as`; these worked live on 2026-09-24 while the `termo_window` shape returned 400. | Responses of exactly 100 rows look like a page cap; the sweep splits by year and warns if still capped. The legacy `/materia/pesquisa/lista` endpoint returns nothing since 2026-02-01. |
| `lexml` | federal_norm / state_norm | documentation | SRU `GET https://www.lexml.gov.br/busca/SRU?operation=searchRetrieve&query=<CQL>`; namespace-agnostic Dublin Core parser; URN resolver URL per record. | Live probe blocked by robots.txt. CQL index names (`localidade`, `urn`) come from the wrapper docs. LexML does not expose vigência: `in_force` is a default, flagged in `extra.status_note`. |
| `inlabs` | federal_regulatory | documentation | Logs in to INLABS, downloads `YYYY-MM-DD-DO1.zip` (+DO1E) per day, parses article XML (`Identifica/Ementa/Texto`), keeps term matches. | Needs `INLABS_EMAIL` / `INLABS_PASSWORD` (free registration) in env or `.env`. Attribute vocabulary is the documented one; parser tolerates missing attributes. |
| `querido_diario` | municipal_gazette | documentation | `GET https://api.queridodiario.ok.org.br/gazettes?querystring="<term>"&territory_ids=…&published_since=…` for the IBGE codes in `sources.json`. | Coverage depends on OKBR scrapers; one hit = one gazette edition. Throttled to 1 req/s. Verify IBGE codes marked `verify=true`. |
| `sapl` | municipal_bill / state_bill | documentation | Generic Interlegis SAPL: `/api/materia/materialegislativa/?ementa__icontains=<term>`; falls back to a year scan filtered client-side if the server rejects or ignores the filter. | No hosts configured by default; pass `--sapl-host https://sapl.<municipio>.<uf>.leg.br`. |
| `alesp` | state_bill (SP) | documentation | Streams `proposituras.zip` -> `proposituras.xml` with `iterparse`; maps fields by tag-name heuristics. | Schema not published; check `extra.raw_keys` on first run. Portal is migrating to `ckan.al.sp.gov.br`. `legislacao_normas` (state laws) not wired yet. |
| `confaz` | state_fiscal | **unverified** | Scrapes the year listing for `CVnnn_yy` links, fetches each convênio, keeps term matches, lists UFs mentioned. | HTML of a Plone site; rows come out `status=unverified`. State adhesion is a hint, not a fact. |
| `almg` | state_bill (MG) | **unverified** | Legacy `/ws/proposicoes/pesquisa/direcionada?expr=&formato=json`; generic list finder. | ALMG has a real Swagger (`/api/ajuda/swagger/view/lastest`) that could not be read; replace the endpoint after checking it. |
| `alrs` | state_bill (RS) | **unverified** | `POST https://ww4.al.rs.gov.br:5000/listaProposicaoCompleto` with `{"ano": …}`; generic list finder. | Internal, undocumented API used by the assembly's own site. |
| `alece` | state_bill (CE) | **unverified** (stub) | Explains that ALECE has no bills API and which substitutes cover CE. | A scraper of `www2.al.ce.gov.br/legislativo/` is the manual path. |

Unverified connectors run only with `--include-unverified`; every row they emit has `verified_adapter=false` and `status=unverified`, and the candidates file excludes them.

## 3. First live run (on the Mac, inside `.venv`)

```bash
cd ~/Projects/Brazil
python scripts/tests/test_policy_corpus.py                 # 54 tests, offline, ~1 s
python scripts/build_policy_corpus.py validate-corpus       # 0 errors expected (3 warnings are known)
python scripts/build_policy_corpus.py sweep --dry-run --connectors camara,senado   # prints planned requests
python scripts/build_policy_corpus.py sweep --connectors camara,senado --since 2024-01-01
python scripts/build_policy_corpus.py recall
```

Then widen: `--connectors camara,senado,lexml,querido_diario,alesp`, and with INLABS credentials in `.env`: `--connectors inlabs --dou-days 60 --dou-sections DO1,DO1E`. Finally `--include-unverified` for CONFAZ / ALMG / AL-RS and inspect `extra.raw_keys` before trusting any of it.

What to look at after the first federal run:

* `manifest.json -> recall`: with Câmara + Senado only, expect hits on PL 278/2026, MPV 1.318/2025, PL 5209/5247/5319 and misses on every enacted norm (those need `lexml`). A recall that drops between runs is the alarm.
* `candidates_for_curation.jsonl`: high-precision matches not in the gold set — read the ementa, decide, and add to the JSON by hand with `status`, `status_note` and `relations`.
* `status_audit` in the manifest: any MP still `proposed` is a bug in either the corpus or the sweep.
* `connectors[].errors`: HTTP 403 from the proxy means the host is blocked on that network; the run is not evidence of "no documents".

## 4. Data model

`documents.jsonl` (one row per unique document):

| field | meaning |
| --- | --- |
| `key` | `urn:lex:…` when the document is a norm; otherwise `<jurisdiction>:<house>:<type>:<number>:<year>` |
| `urn` | LexML identifier; full date when known (`…:2026-09-15;15504`), year-only otherwise (`…:2026;15504`). Both forms compare equal in recall and linking. |
| `layer` | `federal_bill`, `federal_norm`, `federal_regulatory`, `state_bill`, `state_norm`, `state_fiscal`, `municipal_gazette`, `municipal_bill`, … |
| `status` | `proposed`, `in_force`, `enacted`, `lapsed`, `archived`, `partially_vetoed`, `veto_overridden`, `suspended`, `revoked`, `sub_judice`, `consultation_open/closed`, `published`, `unknown`, `unverified` |
| `text_variant` | `as_published`, `original`, `compilado`, `veto_override_promulgation` — a norm's text differs by variant (Lei 15.190/2025) |
| `matched_terms`, `match_strength` | which PT-BR terms fired and whether the acceptance rule was met via a high-precision term or a medium combination |
| `retrieved_at`, `http_status`, `sha256_bytes`, `sha256_text`, `encoding` | provenance; bytes and normalised-text hashes are separate so "source changed" is distinguishable from "decoder changed" |
| `verified_adapter` | false for rows from unverified adapters |
| `extra` | connector-specific fields (situação, DOU section, excerpts, raw keys, `adapter_verification`) |

`relations.jsonl`: `src`, `dst`, `type` (`amends`, `revokes`, `regulates`, `converted_from`, `enacted_as`, `replaces`, `suspended_by`, `challenged_by`, `promulgated_parts_of`, `adopted_by`, `derived_from_consultation`, `cites`), `evidence`, `source` (`senado`, `linking`, `curated`). Endpoints are rewritten onto swept keys when the recall check matched the gold row; unresolved endpoints stay as `urn:lex:…` (resolvable at `lexml.gov.br/urn/<urn>`) or `gold:<ID>`.

`corpus.sqlite`: `documents`, `relations`, and `documents_fts` (FTS5 over title/ementa/excerpt) — `SELECT key FROM documents_fts WHERE documents_fts MATCH 'eficiência AND hídrica'`.

## 5. Term set and acceptance rule

Query terms sent to APIs: `data center`, `datacenter`, `centro de dados`, `centro de processamento de dados`, `redata`. Client-side acceptance (applied identically to every source): one HIGH term (`data center|datacenter|data centre`, `centro(s) de dados`, `centro(s) de processamento de dados`, `redata`, `serviços de datacenter`), or two MEDIUM terms (`hiperescala`, `computação em nuvem`, `eficiência hídrica`, `infraestrutura digital`, `WUE|PUE` case-sensitive), or one MEDIUM plus one LOW (`CPD` case-sensitive, `grandes cargas`, `consumidor livre`, `resfriamento`). LOW terms alone never match. Matching is accent- and case-insensitive except for the acronyms.

## 6. Edge cases the code handles on purpose

* **Lapsed MPs.** `status=lapsed` is a first-class value; `run_scn_phase3_policy.py` now reports `has_redata_core_law_in_force`, which is `false` for the pre-2026-09 corpus.
* **Same act, two spellings.** `MPV` (house sigla) and `medida provisoria` (LexML type) fold to one key in recall and linking; bills (`PL`, `PLP`) are never folded into the norms they may become.
* **Number formatting.** `1.318`, `1318`, `0278` all normalise; thousands separators are the norm on Planalto and absent in APIs.
* **Page caps.** Senado responses of 100 rows trigger a year split and a warning; Câmara pagination follows `rel=next` with a `--max-pages` guard.
* **Deprecated endpoint.** The Senado `materia/pesquisa/lista` service returns an empty envelope with a deactivation date; the connector never calls it.
* **Encoding.** Planalto pages are windows-1252 without a charset header; the decode chain records which encoding won (`encoding` column).
* **Login pages instead of ZIPs.** INLABS answers HTML when the session expires; the connector checks the `PK` magic and reports it instead of parsing garbage.
* **Missing editions.** A 404 from INLABS on a weekend/holiday is expected and silent.
* **Impossible dates** in DOU headers (`31 DE FEVEREIRO`) do not crash the parser; the row keeps the edition date.
* **Excerpt truncation** in Querido Diário: an API hit whose excerpt no longer contains the phrase is kept as `medium` with `matched_terms=["api:…"]` so a reviewer sees it.
* **Unknown SAPL filters**: HTTP 400 or an ignored filter triggers the client-side fallback.
* **Rejected request shapes.** Both federal APIs answered 400 to the fullest request on the first live run; the connectors now try progressively simpler shapes, keep the date window by filtering client-side, and write the accepted shape plus the rejected ones' API messages into the manifest notes.
* **One broken adapter** never kills the sweep: crashes are captured per connector in `manifest.json`.
* **Offline determinism**: `--offline` replays from `raw_cache` and produced byte-identical `documents.jsonl` in the fixture-backed end-to-end test.

## 7. Known gaps (deliberate, documented)

* `pdftotext`-style full-text extraction of `urlInteiroTeor` / `sdleg-getter` PDFs is not wired; `text_url` is recorded so a later step can fetch and hash the full text.
* Jurisprudence (STF ADIs on Lei 14.701/2023 and Lei 15.190/2025) is represented only as `litigation_note` / `status_note` text in the curated JSON; there is no STF connector.
* State laws (as opposed to bills) for SP/MG/RS/CE come only through the LexML `localidade` filter, whose index name is documentation-level.
* Convênio ICMS 38/2026 content and the per-state adhesion list were not read; the curated row is `unverified` until someone opens the CONFAZ page.
* `Lei 15.190/2025` sanction day (2025-08-08) and the Planalto URL casing were not re-verified; Decreto 12.772/2025's Planalto URL and ANEEL REN 1.122/2025's CEDOC URL follow the standard patterns but were not opened.
