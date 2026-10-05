"""Offline test-suite for scripts/policy_corpus.

Run from the repository root with either of:

    python -m pytest scripts/tests -q
    python scripts/tests/test_policy_corpus.py        # plain unittest, no pytest needed

Every connector is exercised through a ``StubFetcher`` that serves the
fixtures in ``scripts/tests/fixtures`` (shaped like the live responses
observed on 2026-09-23), so the suite needs no network.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from policy_corpus import linking, recall, store, validate  # noqa: E402
from policy_corpus.connectors import Context, camara, confaz, inlabs, lexml, querido_diario, sapl, senado, states  # noqa: E402
from policy_corpus.fetch import Fetcher, FetchResult, OfflineMiss  # noqa: E402
from policy_corpus.models import (  # noqa: E402
    Document,
    Layer,
    Relation,
    RelationType,
    Status,
    build_urn,
    dedupe_documents,
    normalize_number,
    parse_identificacao,
)
from policy_corpus.terms import match_terms  # noqa: E402

FIX = HERE / "fixtures"


def fixture(name: str) -> bytes:
    return (FIX / name).read_bytes()


class StubFetcher(Fetcher):
    """Serve canned responses; record every request. ``routes`` maps a
    predicate over (method, url, params, body) to a (status, bytes, headers)."""

    def __init__(self, routes: list[tuple[Any, tuple[int, bytes, dict[str, str]]]], tmp: Path):
        super().__init__(cache_dir=tmp / "cache", default_throttle=0.0)
        self.routes = routes
        self.calls: list[dict[str, Any]] = []

    def request(self, method, url, params=None, headers=None, json_body=None, data=None, allow_cache=True):  # type: ignore[override]
        self.calls.append({"method": method, "url": url, "params": params, "json": json_body, "data": data})
        for pred, (status, body, hdrs) in self.routes:
            if pred(method, url, params or {}, json_body or data):
                return FetchResult(url=url, status=status, content=body, headers=hdrs, retrieved_at="2026-09-23T00:00:00+00:00")
        return FetchResult(url=url, status=404, content=b"", headers={}, retrieved_at="2026-09-23T00:00:00+00:00")


def make_ctx(fetcher: Fetcher, **overrides: Any) -> Context:
    cfg = json.loads((ROOT / "scripts" / "policy_corpus" / "sources.json").read_text(encoding="utf-8"))
    base = dict(
        fetcher=fetcher,
        since=dt.date(2025, 1, 1),
        until=dt.date(2026, 9, 23),
        config=cfg,
        states=["CE", "SP", "MG", "RS"],
        log=logging.getLogger("test"),
        query_terms=("data center", "redata"),
        max_pages=5,
        detail=True,
        include_unverified=True,
        options={},
    )
    base.update(overrides)
    return Context(**base)


JSON_H = {"content-type": "application/json; charset=utf-8"}
XML_H = {"content-type": "application/xml; charset=utf-8"}
HTML_H = {"content-type": "text/html; charset=utf-8"}


# ================================================================== models
class ModelTests(unittest.TestCase):
    def test_normalize_number(self):
        self.assertEqual(normalize_number("1.318"), "1318")
        self.assertEqual(normalize_number("0278"), "278")
        self.assertEqual(normalize_number(" 15.504 "), "15504")
        self.assertEqual(normalize_number("000"), "0")
        self.assertEqual(normalize_number(None), "")
        self.assertEqual(normalize_number(12), "12")

    def test_build_urn(self):
        self.assertEqual(build_urn("Lei", "15.504", 2026, "2026-09-15"), "urn:lex:br:federal:lei:2026-09-15;15504")
        self.assertEqual(build_urn("MPV", "1318", 2025), "urn:lex:br:federal:medida.provisoria:2025;1318")
        self.assertEqual(build_urn("lei complementar", "140", 2011, "2011-12-08"), "urn:lex:br:federal:lei.complementar:2011-12-08;140")
        self.assertEqual(build_urn("lei", "12", 2010, None, "estadual", "sao.paulo"), "urn:lex:br;sao.paulo:estadual:lei:2010;12")
        self.assertIsNone(build_urn("pl", "278", 2026), "bills have no LexML norm URN")
        self.assertIsNone(build_urn("lei", "", 2026))
        self.assertIsNone(build_urn("lei", "1", None, None))
        self.assertEqual(build_urn("lei", "1", 2020, "not-a-date"), "urn:lex:br:federal:lei:2020;1", "bad date falls back to year")

    def test_parse_identificacao(self):
        self.assertEqual(parse_identificacao("PL 278/2026"), ("PL", "278", 2026))
        self.assertEqual(parse_identificacao("MPV 1.318/2025"), ("MPV", "1318", 2025))
        self.assertEqual(parse_identificacao("  PLP 12/2024 "), ("PLP", "12", 2024))
        self.assertIsNone(parse_identificacao(""))
        self.assertIsNone(parse_identificacao("Requerimento"))

    def test_document_key_and_urn_autobuild(self):
        d = Document(source="x", layer=Layer.FEDERAL_NORM, jurisdiction="BR", doc_type="Lei", number="15.504", year=2026, title="t", date="2026-09-15")
        self.assertEqual(d.urn, "urn:lex:br:federal:lei:2026-09-15;15504")
        self.assertEqual(d.key, d.urn)
        b = Document(source="camara", layer="federal_bill", jurisdiction="BR", doc_type="PL", number="0278", year="2026", title="t", house="CD", status="proposed")
        self.assertIsNone(b.urn)
        self.assertEqual(b.key, "br:cd:pl:278:2026")
        self.assertEqual(b.status, Status.PROPOSED)
        self.assertEqual(b.layer, Layer.FEDERAL_BILL)

    def test_invalid_status_rejected(self):
        with self.assertRaises(ValueError):
            Document(source="x", layer=Layer.FEDERAL_BILL, jurisdiction="BR", doc_type="pl", number="1", year=2026, title="t", status="approved-ish")

    def test_dedupe_merges_terms_and_fills_fields(self):
        a = Document(source="lexml", layer=Layer.FEDERAL_NORM, jurisdiction="BR", doc_type="lei", number="15504", year=2026, title="A", date="2026-09-15", matched_terms=["redata"], match_strength="high")
        b = Document(source="planalto", layer=Layer.FEDERAL_NORM, jurisdiction="BR", doc_type="lei", number="15.504", year=2026, title="B", date="2026-09-15", ementa="Altera...", matched_terms=["data center"], match_strength="high", url="https://planalto")
        uniq, merged = dedupe_documents([a, b])
        self.assertEqual(merged, 1)
        self.assertEqual(len(uniq), 1)
        self.assertEqual(sorted(uniq[0].matched_terms), ["data center", "redata"])
        self.assertEqual(uniq[0].ementa, "Altera...")
        self.assertEqual(uniq[0].url, "https://planalto")
        self.assertEqual(uniq[0].extra["also_seen_in"], "planalto")


# =================================================================== terms
class TermTests(unittest.TestCase):
    def test_high_terms(self):
        for text in ("Serviços de Datacenter (Redata)", "data-center", "Data Centre", "centros de dados", "centro de processamento de dados"):
            _, strength = match_terms(text)
            self.assertEqual(strength, "high", text)

    def test_low_alone_is_none(self):
        self.assertEqual(match_terms("grandes cargas na rede básica")[1], "none")
        self.assertEqual(match_terms("consumidor livre")[1], "none")
        self.assertEqual(match_terms("o CPD da empresa")[1], "none")

    def test_medium_combination(self):
        self.assertEqual(match_terms("Eficiência hídrica e resfriamento")[1], "medium")
        self.assertEqual(match_terms("hiperescala com computação em nuvem")[1], "medium")
        self.assertEqual(match_terms("apenas hiperescala")[1], "none")

    def test_case_sensitive_acronyms(self):
        self.assertIn("WUE/PUE", match_terms("índice PUE")[0])
        self.assertNotIn("WUE/PUE", match_terms("pue minúsculo")[0])
        self.assertIn("CPD", match_terms("o CPD central")[0])
        self.assertNotIn("CPD", match_terms("cpd")[0])

    def test_accent_insensitive(self):
        self.assertEqual(match_terms("EFICIÊNCIA HÍDRICA e RESFRIAMENTO")[1], "medium")
        self.assertEqual(match_terms("")[1], "none")
        self.assertEqual(match_terms(None, "")[1], "none")


# =================================================================== fetch
class FetchTests(unittest.TestCase):
    def test_encoding_chain(self):
        cp = "Institui a Política de Data Centers".encode("cp1252")
        r = FetchResult("u", 200, cp, {}, "t")
        self.assertEqual(r.text, "Institui a Política de Data Centers")
        self.assertEqual(r.encoding_used, "cp1252")
        r2 = FetchResult("u", 200, "Política".encode("utf-8"), {"content-type": "text/html; charset=utf-8"}, "t")
        self.assertEqual(r2.text, "Política")
        self.assertEqual(r2.encoding_used, "utf-8")
        r3 = FetchResult("u", 200, b'<?xml version="1.0" encoding="ISO-8859-1"?><a>Pol\xedtica</a>', {}, "t")
        self.assertIn("Política", r3.text)
        self.assertEqual(r3.encoding_used, "iso-8859-1")
        r4 = FetchResult("u", 200, b"", {}, "t")
        self.assertEqual(r4.text, "")
        self.assertIsNone(r4.json())

    def test_cache_roundtrip_and_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Fetcher(cache_dir=Path(tmp), default_throttle=0.0)
            calls = {"n": 0}

            class Resp:
                url = "https://example.org/x?a=1"
                status_code = 200
                content = b'{"ok": true}'
                headers = {"Content-Type": "application/json"}

            def fake_request(method, url, **kw):
                calls["n"] += 1
                return Resp()

            f.session.request = fake_request  # type: ignore[assignment]
            r1 = f.get("https://example.org/x", params={"a": 1})
            r2 = f.get("https://example.org/x", params={"a": 1})
            self.assertEqual(calls["n"], 1, "second call served from cache")
            self.assertTrue(r2.from_cache)
            self.assertEqual(r1.json(), {"ok": True})
            off = Fetcher(cache_dir=Path(tmp), offline=True)
            self.assertEqual(off.get("https://example.org/x", params={"a": 1}).json(), {"ok": True})
            with self.assertRaises(OfflineMiss):
                off.get("https://example.org/other")

    def test_dry_run_records_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Fetcher(cache_dir=Path(tmp), dry_run=True)
            r = f.get("https://example.org/x", params={"q": "data center"})
            self.assertEqual(r.error, "dry_run")
            self.assertFalse(r.ok)
            self.assertEqual(f.planned[0]["params"], {"q": "data center"})


# ================================================================== camara
class CamaraTests(unittest.TestCase):
    def routes(self):
        return [
            (lambda m, u, p, b: u.endswith("/proposicoes") and p.get("keywords") == "data center", (200, fixture("camara_list_page1.json"), JSON_H)),
            (lambda m, u, p, b: "pagina=2" in u, (200, fixture("camara_list_page2.json"), JSON_H)),
            (lambda m, u, p, b: u.endswith("/proposicoes") and p.get("keywords") == "redata", (200, b'{"dados": [], "links": []}', JSON_H)),
            (lambda m, u, p, b: u.endswith("/proposicoes/2600838"), (200, fixture("camara_detail_2600838.json"), JSON_H)),
            (lambda m, u, p, b: u.endswith("/proposicoes/2650000"), (200, fixture("camara_detail_2650000.json"), JSON_H)),
            (lambda m, u, p, b: u.endswith("/proposicoes/2700001"), (200, b'{"dados": {"id": 2700001, "statusProposicao": {"descricaoSituacao": "Aguardando Parecer"}}}', JSON_H)),
            (lambda m, u, p, b: u.endswith("/proposicoes/2700002"), (500, b"boom", {})),
        ]

    def test_sweep_pagination_status_and_near_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = StubFetcher(self.routes(), Path(tmp))
            res = camara.sweep(make_ctx(f))
        titles = {d.title: d for d in res.documents}
        self.assertIn("PL 278/2026", titles)
        self.assertIn("MPV 1318/2025", titles)
        self.assertIn("PL 5209/2026", titles)
        self.assertNotIn("PL 9999/2026", titles, "tarifa social must fail the term rule")
        self.assertEqual(titles["PL 278/2026"].status, Status.ENACTED)
        self.assertEqual(titles["MPV 1318/2025"].status, Status.LAPSED)
        self.assertEqual(titles["PL 5209/2026"].status, Status.PROPOSED)
        self.assertEqual(titles["PL 278/2026"].text_url, "https://www.camara.leg.br/proposicoesWeb/prop_mostrarintegra?codteor=2900000")
        self.assertTrue(any("near-miss" in n for n in res.notes))
        self.assertTrue(any("detail 2700002" in e for e in res.errors), "500 on detail is recorded, not fatal")
        self.assertEqual(titles["PL 278/2026"].key, "br:cd:pl:278:2026")

    def test_parse_list_page_tolerates_empty(self):
        self.assertEqual(camara.parse_list_page(None), ([], None))
        self.assertEqual(camara.parse_list_page({"dados": [], "links": [{"rel": "self", "href": "x"}]}), ([], None))


# ================================================================== senado
class SenadoTests(unittest.TestCase):
    def test_document_parsing_and_status(self):
        rows = json.loads(fixture("senado_processo_termo.json"))
        d0 = senado.document_from_item(rows[0])
        self.assertEqual((d0.doc_type, d0.number, d0.year, d0.house), ("pl", "278", 2026, "CD"))
        self.assertEqual(d0.status, Status.PROPOSED)
        self.assertEqual(d0.url, "https://www25.senado.leg.br/web/atividade/materias/-/materia/172786")
        d1 = senado.document_from_item(rows[1])
        self.assertEqual(d1.status, Status.ARCHIVED)
        d2 = senado.document_from_item(rows[2])
        self.assertEqual(d2.doc_type, "processo")
        self.assertEqual(d2.year, 2025, "year recovered from dataApresentacao when identificacao is empty")

    def test_sweep_norm_lookup_relation_and_cap_split(self):
        big = json.dumps([dict(json.loads(fixture("senado_processo_termo.json"))[0], id=i, identificacao=f"PL {i}/2026", codigoMateria=i) for i in range(100)]).encode()
        routes = [
            (lambda m, u, p, b: p.get("termo") == "data center" and p.get("dataInicioApresentacao") == "2025-01-01" and p.get("dataFimApresentacao") == "2026-09-23", (200, big, JSON_H)),
            (lambda m, u, p, b: p.get("termo") == "data center" and p.get("ano") == 2025, (200, b"[]", JSON_H)),
            (lambda m, u, p, b: p.get("termo") == "data center" and p.get("ano") == 2026, (200, fixture("senado_processo_termo.json"), JSON_H)),
            (lambda m, u, p, b: p.get("termo") == "redata", (200, b"[]", JSON_H)),
            (lambda m, u, p, b: p.get("tipoNorma") == "LEI" and p.get("numeroNorma") == "15504", (200, fixture("senado_processo_norma.json"), JSON_H)),
            (lambda m, u, p, b: p.get("tipoNorma") == "LEI", (200, b"[]", JSON_H)),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            f = StubFetcher(routes, Path(tmp))
            res = senado.sweep(make_ctx(f))
        self.assertTrue(any("splitting by year" in n for n in res.notes))
        keys = {d.key for d in res.documents}
        self.assertIn("br:cd:pl:278:2026", keys)
        self.assertNotIn("br:sf:processo:na:2025", keys, "generic requerimento fails the term rule")
        enacted = [r for r in res.relations if r.type == RelationType.ENACTED_AS]
        self.assertEqual(len(enacted), 1)
        self.assertEqual(enacted[0].dst, "urn:lex:br:federal:lei:2026;15504")
        pl = next(d for d in res.documents if d.key == "br:cd:pl:278:2026")
        self.assertEqual(pl.status, Status.PROPOSED, "first-seen row wins; status from norm lookup does not overwrite")


# =================================================================== lexml
class LexmlTests(unittest.TestCase):
    def test_parse_sru_and_documents(self):
        total, recs, nxt = lexml.parse_sru(fixture("lexml_sru.xml").decode())
        self.assertEqual(total, 2)
        self.assertIsNone(nxt)
        fed = lexml.document_from_record(recs[0])
        self.assertEqual(fed.urn, "urn:lex:br:federal:lei:2026-09-15;15504")
        self.assertEqual((fed.doc_type, fed.number, fed.year, fed.date), ("lei", "15504", 2026, "2026-09-15"))
        self.assertEqual(fed.url, "https://www.lexml.gov.br/urn/urn:lex:br:federal:lei:2026-09-15;15504")
        self.assertEqual(fed.match_strength, "high")
        sp = lexml.document_from_record(recs[1])
        self.assertEqual(sp.jurisdiction, "BR-SP")
        self.assertEqual(sp.layer, Layer.STATE_NORM)

    def test_diagnostic_raises(self):
        with self.assertRaises(ValueError):
            lexml.parse_sru(fixture("lexml_diagnostic.xml").decode())

    def test_split_urn(self):
        p = lexml.split_urn("urn:lex:br;sao.paulo:estadual:lei:2026-03-01;17000@versao")
        self.assertEqual((p["jurisdiction"], p["locality"], p["authority"], p["type"], p["date"], p["number"]), ("br", "sao.paulo", "estadual", "lei", "2026-03-01", "17000"))
        self.assertEqual(lexml.split_urn("urn:lex:br:federal:lei:2026;15504")["number"], "15504")
        self.assertEqual(lexml.split_urn("garbage")["type"], "")

    def test_sweep_uses_config_urn_lookups(self):
        routes = [(lambda m, u, p, b: "SRU" in u, (200, fixture("lexml_sru.xml"), XML_H))]
        with tempfile.TemporaryDirectory() as tmp:
            f = StubFetcher(routes, Path(tmp))
            res = lexml.sweep(make_ctx(f, states=["SP"]))
        self.assertEqual(len(res.documents), 2)
        queries = [c["params"]["query"] for c in f.calls]
        self.assertTrue(any('urn = "urn:lex:br:federal:lei:2026-09-15;15504"' == q for q in queries))
        self.assertTrue(any("localidade" in q for q in queries))


# ================================================================== inlabs
class InlabsTests(unittest.TestCase):
    def test_parse_identifica(self):
        self.assertEqual(inlabs.parse_identifica("PORTARIA MME Nº 1.234, DE 22 DE SETEMBRO DE 2026"), ("1234", "2026-09-22"))
        self.assertEqual(inlabs.parse_identifica("DECRETO Nº 12.772, DE 5 DE DEZEMBRO DE 2025"), ("12772", "2025-12-05"))
        self.assertEqual(inlabs.parse_identifica("PORTARIA DE 31 DE FEVEREIRO DE 2026"), ("", None), "impossible date -> None, no crash")
        self.assertEqual(inlabs.parse_identifica(""), ("", None))

    def test_article_zip_roundtrip_and_filter(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("2026-09-23/a.xml", fixture("inlabs_article.xml"))
            zf.writestr("2026-09-23/b.xml", fixture("inlabs_article_irrelevant.xml"))
            zf.writestr("readme.txt", b"ignored")
        arts = list(inlabs.iter_articles(buf.getvalue()))
        self.assertEqual(len(arts), 2)
        doc = inlabs.document_from_article(arts[0][1], dt.date(2026, 9, 23), "DO1")
        self.assertEqual((doc.doc_type, doc.number, doc.date, doc.issuer), ("portaria", "1234", "2026-09-22", "Ministério de Minas e Energia"))
        self.assertEqual(doc.match_strength, "high")
        self.assertIn("centros de dados", doc.extra["text_excerpt"])
        self.assertEqual(doc.url, "https://www.in.gov.br/web/dou/-/portaria-mme-n-1.234-de-22-de-setembro-de-2026-654321")
        irrelevant = inlabs.document_from_article(arts[1][1], dt.date(2026, 9, 23), "DO1")
        self.assertEqual(irrelevant.match_strength, "none")

    def test_sweep_without_credentials(self):
        import os
        old = {k: os.environ.pop(k, None) for k in ("INLABS_EMAIL", "INLABS_PASSWORD")}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                res = inlabs.sweep(make_ctx(StubFetcher([], Path(tmp))))
            self.assertTrue(res.errors and "INLABS_EMAIL" in res.errors[0])
            self.assertEqual(res.documents, [])
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v

    def test_sweep_offline_with_cached_zip(self):
        import os
        os.environ["INLABS_EMAIL"], os.environ["INLABS_PASSWORD"] = "x@y", "z"
        try:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("a.xml", fixture("inlabs_article.xml"))
            routes = [
                (lambda m, u, p, b: p.get("dl") == "2026-09-23-DO1.zip", (200, buf.getvalue(), {"content-type": "application/zip"})),
                (lambda m, u, p, b: p.get("dl", "").endswith("DO1E.zip"), (404, b"", {})),
                (lambda m, u, p, b: p.get("dl") == "2026-09-22-DO1.zip", (200, b"<html>login</html>", HTML_H)),
            ]
            with tempfile.TemporaryDirectory() as tmp:
                f = StubFetcher(routes, Path(tmp))
                f.offline = True  # skip login
                res = inlabs.sweep(make_ctx(f, since=dt.date(2026, 9, 22), until=dt.date(2026, 9, 23), options={"dou_days": 2}))
            self.assertEqual(len(res.documents), 1)
            self.assertTrue(any("not a ZIP" in e for e in res.errors))
        finally:
            os.environ.pop("INLABS_EMAIL", None)
            os.environ.pop("INLABS_PASSWORD", None)


# ================================================================== confaz
class ConfazTests(unittest.TestCase):
    def test_listing_and_convenio_parsing(self):
        entries = confaz.parse_listing(fixture("confaz_listing.html").decode())
        self.assertEqual([e[2] for e in entries], [38, 1], "dedup + script links ignored")
        parsed = confaz.parse_convenio(fixture("confaz_convenio.html").decode())
        self.assertEqual(parsed["number"], "38")
        self.assertEqual(parsed["states"], ["CE", "MG", "RS", "SP"])

    def test_sweep_marks_unverified(self):
        routes = [
            (lambda m, u, p, b: u.endswith("/convenios/2026"), (200, fixture("confaz_listing.html"), HTML_H)),
            (lambda m, u, p, b: u.endswith("/convenios/2025"), (200, b"<html></html>", HTML_H)),
            (lambda m, u, p, b: u.endswith("CV038_26"), (200, fixture("confaz_convenio.html"), HTML_H)),
            (lambda m, u, p, b: u.endswith("CV001_26"), (200, b"<html><h1>CONVENIO ICMS N 1, DE 1 DE JANEIRO DE 2026</h1><p>Outro assunto.</p></html>", HTML_H)),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            res = confaz.sweep(make_ctx(StubFetcher(routes, Path(tmp))))
        self.assertEqual(len(res.documents), 1)
        d = res.documents[0]
        self.assertEqual(d.status, Status.UNVERIFIED)
        self.assertFalse(d.verified_adapter)
        self.assertEqual(d.key, "br:confaz:convenio icms:38:2026")


# ========================================================== querido diário
class QueridoDiarioTests(unittest.TestCase):
    def test_parse_and_document(self):
        total, gz = querido_diario.parse_response(json.loads(fixture("querido_diario.json")))
        self.assertEqual(total, 2)
        d0 = querido_diario.document_from_gazette(gz[0], '"data center"')
        self.assertEqual(d0.jurisdiction, "BR-CE-2304400")
        self.assertEqual(d0.match_strength, "high")
        self.assertEqual(d0.text_url.endswith(".txt"), True)
        d1 = querido_diario.document_from_gazette(gz[1], '"data center"')
        self.assertEqual(d1.match_strength, "medium", "API hit with truncated excerpt is kept for review")
        self.assertEqual(d1.matched_terms, ['api:"data center"'])

    def test_sweep_validates_ibge_codes(self):
        routes = [(lambda m, u, p, b: u.endswith("/gazettes"), (200, fixture("querido_diario.json"), JSON_H))]
        with tempfile.TemporaryDirectory() as tmp:
            f = StubFetcher(routes, Path(tmp))
            res = querido_diario.sweep(make_ctx(f, states=["CE"], options={"municipalities": ["123", "3550308"]}))
        self.assertTrue(any("7 digits" in e for e in res.errors))
        sent = f.calls[0]["params"]["territory_ids"]
        self.assertIn("3550308", sent)
        self.assertNotIn("123", sent)
        self.assertEqual(len(res.documents), 2)


# ==================================================================== sapl
class SaplTests(unittest.TestCase):
    def test_server_filter_then_fallback(self):
        routes = [
            (lambda m, u, p, b: "ementa__icontains" in p, (400, b'{"detail": "unknown filter"}', JSON_H)),
            (lambda m, u, p, b: p.get("ano") == 2026, (200, fixture("sapl_page.json"), JSON_H)),
            (lambda m, u, p, b: "ano" in p, (200, b'{"count": 0, "next": null, "results": []}', JSON_H)),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            f = StubFetcher(routes, Path(tmp))
            res = sapl.sweep(make_ctx(f, options={"sapl_hosts": ["https://sapl.exemplo.ce.leg.br"]}))
        self.assertEqual(len(res.documents), 1)
        d = res.documents[0]
        self.assertEqual((d.doc_type, d.number, d.year), ("pl", "12", 2026))
        self.assertEqual(d.url, "https://sapl.exemplo.ce.leg.br/materia/501")
        self.assertEqual(d.status, Status.PROPOSED)

    def test_no_hosts_is_a_note_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = sapl.sweep(make_ctx(StubFetcher([], Path(tmp)), config={"sapl_hosts": []}))
        self.assertEqual(res.errors, [])
        self.assertTrue(res.notes)


# ================================================================== states
class StatesTests(unittest.TestCase):
    def test_alesp_zip_stream(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("proposituras.xml", fixture("alesp_proposituras.xml"))
        routes = [(lambda m, u, p, b: u.endswith("proposituras.zip"), (200, buf.getvalue(), {"content-type": "application/zip"}))]
        with tempfile.TemporaryDirectory() as tmp:
            res = states.sweep_alesp(make_ctx(StubFetcher(routes, Path(tmp))))
        self.assertEqual(len(res.documents), 1, "2019 record outside window; 'utilidade pública' fails term rule")
        d = res.documents[0]
        self.assertEqual((d.doc_type, d.number, d.year, d.date), ("pl", "42", 2026, "2026-03-03"))
        self.assertEqual(d.url, "https://www.al.sp.gov.br/propositura/?id=1000001")
        self.assertEqual(d.jurisdiction, "BR-SP")

    def test_alesp_not_a_zip(self):
        routes = [(lambda m, u, p, b: True, (200, b"<html>moved</html>", HTML_H))]
        with tempfile.TemporaryDirectory() as tmp:
            res = states.sweep_alesp(make_ctx(StubFetcher(routes, Path(tmp))))
        self.assertTrue(any("not a ZIP" in e for e in res.errors))

    def test_state_gating_by_states_arg(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = StubFetcher([], Path(tmp))
            self.assertIn("skipped", states.sweep_alesp(make_ctx(f, states=["CE"])).notes[0])
            self.assertIn("skipped", states.sweep_almg(make_ctx(f, states=["CE"])).notes[0])
            self.assertIn("skipped", states.sweep_alrs(make_ctx(f, states=["CE"])).notes[0])
            self.assertIn("ALECE", states.sweep_alece(make_ctx(f, states=["CE"])).notes[0])

    def test_generic_list_finder_and_unverified_rows(self):
        payload = {"resultado": {"listaItem": [{"id": 7, "siglaTipo": "PL", "numero": 100, "ano": 2026, "ementa": "Cria o polo de data centers de Minas."}]}}
        self.assertEqual(len(states._find_list(payload)), 1)
        routes = [(lambda m, u, p, b: "almg" in u, (200, json.dumps(payload).encode(), JSON_H))]
        with tempfile.TemporaryDirectory() as tmp:
            res = states.sweep_almg(make_ctx(StubFetcher(routes, Path(tmp)), states=["MG"]))
        self.assertEqual(len(res.documents), 1)
        self.assertEqual(res.documents[0].status, Status.UNVERIFIED)
        self.assertFalse(res.documents[0].verified_adapter)


# ================================================================== recall
class RecallTests(unittest.TestCase):
    def test_gold_key_title_parsing(self):
        cases = {
            "Projeto de Lei No. 278/2026 - Senado Federal": ("pl", "278", 2026),
            "Medida Provisoria No. 1.318/2025 - REDATA": ("medida provisoria", "1318", 2025),
            "Lei Complementar No. 140/2011 - environmental competence": ("lei complementar", "140", 2011),
            "Lei No. 6.001/1973 - Estatuto do Indio": ("lei", "6001", 1973),
            "Decreto No. 10.088/2019 - ILO": ("decreto", "10088", 2019),
            "Resolucao CONAMA No. 428/2010 - licenciamento": ("resolucao", "428", 2010),
            "Convenio ICMS No. 38/2026 - CONFAZ": ("convenio icms", "38", 2026),
        }
        for title, expected in cases.items():
            k = recall.gold_key({"title": title, "type": "federal_law"})
            self.assertEqual((k["doc_type"], k["number"], k["year"]), expected, title)
        self.assertFalse(recall.gold_key({"title": "White Paper", "type": "government_white_paper"})["discoverable"])
        self.assertTrue(recall.gold_key({"title": "Lei No. 1/2000", "type": "federal_law"})["discoverable"])

    def test_compute_recall_matching_paths(self):
        corpus = [
            Document(source="lexml", layer=Layer.FEDERAL_NORM, jurisdiction="BR", doc_type="lei", number="15504", year=2026, title="Lei", date="2026-09-15"),
            Document(source="camara", layer=Layer.FEDERAL_BILL, jurisdiction="BR", doc_type="pl", number="278", year=2026, title="PL", house="CD"),
            Document(source="x", layer=Layer.GOVERNMENT_COMMUNICATION, jurisdiction="BR", doc_type="page", number="", year=None, title="p", url="https://www.gov.br/mme/redata/"),
        ]
        gold = [
            {"id": "LEI", "title": "Lei No. 15.504/2026 - Redata", "type": "federal_law", "category": "core", "urn": "urn:lex:br:federal:lei:2026;15504"},
            {"id": "PL", "title": "Projeto de Lei No. 278/2026", "type": "legislative_docket", "category": "core"},
            {"id": "MP", "title": "Medida Provisoria No. 1.318/2025", "type": "government_legal_text", "category": "core"},
            {"id": "NEWS", "title": "MP cria o Redata", "type": "government_news", "category": "ctx", "url": "https://www.gov.br/mme/redata/"},
        ]
        rep = recall.compute_recall(gold, corpus)
        self.assertEqual(rep["hits"], 2)
        self.assertEqual(rep["misses"], 1)
        self.assertEqual(rep["not_discoverable"], 1)
        self.assertAlmostEqual(rep["recall"], 2 / 3)
        self.assertEqual({h["gold_id"]: h["matched_by"] for h in rep["hit_detail"]}, {"LEI": "urn", "PL": "type_number_year"})
        self.assertEqual(rep["miss_detail"][0]["gold_id"], "MP")
        self.assertIsNone(recall.compute_recall([], corpus)["recall"])


# ================================================================ validate
class ValidateTests(unittest.TestCase):
    def _write(self, tmp: Path, payload: dict) -> Path:
        p = tmp / "c.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    def test_updated_curated_corpus_passes(self):
        errors, warnings, summary = validate.validate_corpus(ROOT / "docs" / "phase3_redata_policy_corpus.json")
        self.assertEqual(errors, [], errors)
        self.assertGreaterEqual(summary["documents"], 47)
        self.assertGreaterEqual(summary["statuses"].get("in_force", 0), 1)

    def test_lapsed_only_core_law_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._write(Path(tmp), {"corpus_name": "x", "last_updated": "2026-09-23", "documents": [
                {"id": "MP", "category": "redata_core_law", "type": "government_legal_text", "title": "MP", "url": "https://x", "phase3_use": "u", "status": "lapsed", "date": "2025-09-17"}
            ]})
            errors, _w, _s = validate.validate_corpus(p)
        self.assertTrue(any("no document with status=in_force" in e for e in errors))

    def test_bad_status_relation_and_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._write(Path(tmp), {"corpus_name": "x", "last_updated": "23/09/2026", "documents": [
                {"id": "A", "category": "c", "type": "t", "title": "T", "url": "http://x", "phase3_use": "u", "status": "approved", "date": "updated",
                 "relations": [{"type": "amends", "target": "MISSING"}, {"type": "bogus", "target": "urn:lex:br:federal:lei:2020;1"}]},
                {"id": "A", "category": "c", "type": "t", "title": "T", "url": "https://y", "phase3_use": "u", "status": "in_force"},
            ]})
            errors, warnings, _s = validate.validate_corpus(p)
        joined = "\n".join(errors)
        self.assertIn("last_updated is not an ISO date", joined)
        self.assertIn("unknown status 'approved'", joined)
        self.assertIn("target 'MISSING'", joined)
        self.assertIn("unknown relation type 'bogus'", joined)
        self.assertIn("duplicate id", joined)
        self.assertTrue(any("http url" in w for w in warnings))
        self.assertTrue(any("non-ISO date" in w for w in warnings))


# ================================================================= linking
class LinkingTests(unittest.TestCase):
    def test_cross_house_mirror_and_replaces(self):
        cd = Document(source="camara", layer=Layer.FEDERAL_BILL, jurisdiction="BR", doc_type="pl", number="278", year=2026, title="PL", house="CD")
        sf = Document(source="senado", layer=Layer.FEDERAL_BILL, jurisdiction="BR", doc_type="pl", number="278", year=2026, title="PL", house="SF")
        mp = Document(source="camara", layer=Layer.FEDERAL_BILL, jurisdiction="BR", doc_type="mpv", number="1318", year=2025, title="MPV", house="CD", status=Status.PROPOSED)
        docs = [cd, sf, mp]
        rels = linking.link_same_bill_across_houses(docs)
        self.assertEqual(len(rels), 1)
        self.assertEqual({rels[0].src, rels[0].dst}, {cd.key, sf.key})
        enacted = [Relation(cd.key, "urn:lex:br:federal:lei:2026;15504", RelationType.ENACTED_AS, "e", "senado")]
        mirror = linking.mirror_enacted(enacted)
        self.assertEqual(mirror[0].type, RelationType.CONVERTED_FROM)
        self.assertEqual(mirror[0].src, "urn:lex:br:federal:lei:2026;15504")
        pairs = [{"lapsed": {"doc_type": "mpv", "number": "1.318", "year": 2025}, "replacement": {"doc_type": "pl", "number": "278", "year": 2026}}]
        rep = linking.replaces_pairs(docs, pairs)
        self.assertEqual(len(rep), 2, "one REPLACES per (replacement, lapsed) pair across both houses")
        self.assertTrue(all(r.type == RelationType.REPLACES for r in rep))
        pairs_urn = [{"lapsed": {"doc_type": "medida provisoria", "number": "1318", "year": 2025, "date": "2025-09-17"}, "replacement": {"doc_type": "pl", "number": "278", "year": 2026}}]
        rep2 = linking.replaces_pairs([cd, sf], pairs_urn)
        self.assertTrue(all(r.dst == "urn:lex:br:federal:medida.provisoria:2025-09-17;1318" for r in rep2))
        self.assertTrue(linking.status_audit(docs)[0].startswith("MPV"))

    def test_curated_relations_import(self):
        gold = [
            {"id": "PL", "relations": [{"type": "enacted_as", "target": "LEI"}, {"type": "nope", "target": "LEI"}]},
            {"id": "LEI", "urn": "urn:lex:br:federal:lei:2026-09-15;15504", "relations": [{"type": "amends", "target": "urn:lex:br:federal:lei:2005-11-21;11196"}]},
        ]
        rels = linking.curated_relations(gold)
        self.assertEqual(len(rels), 2)
        self.assertEqual(rels[0].src, "gold:PL")
        self.assertEqual(rels[0].dst, "urn:lex:br:federal:lei:2026-09-15;15504")


# =================================================================== store
class StoreTests(unittest.TestCase):
    def test_jsonl_sqlite_and_parquet(self):
        docs = [Document(source="lexml", layer=Layer.FEDERAL_NORM, jurisdiction="BR", doc_type="lei", number="15504", year=2026, title="Lei 15.504/2026", ementa="Redata", date="2026-09-15", extra={"text_excerpt": "Serviços de Datacenter"})]
        rels = [Relation(docs[0].key, "gold:PL", RelationType.CONVERTED_FROM, "e", "t")]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            paths = store.write_outputs(out, docs, rels, {"run": {}})
            rows = store.read_jsonl(out / "documents.jsonl")
            self.assertEqual(rows[0]["key"], docs[0].urn)
            self.assertEqual(rows[0]["status"], "unknown", "status is never assumed; connectors set it explicitly")
            import sqlite3
            con = sqlite3.connect(out / "corpus.sqlite")
            try:
                self.assertEqual(con.execute("select count(*) from relations").fetchone()[0], 1)
                try:
                    hit = con.execute("select key from documents_fts where documents_fts match 'Datacenter'").fetchone()
                    self.assertEqual(hit[0], docs[0].urn)
                except sqlite3.OperationalError:
                    pass  # FTS5 not compiled in; tables still exist
            finally:
                con.close()
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("documents_jsonl", manifest["outputs"])
            wrote = store.write_parquet(out / "p.parquet", [d.to_record() for d in docs])
            self.assertIsInstance(wrote, bool)
            if wrote:
                self.assertTrue((out / "p.parquet").exists())
            (out / "bad.jsonl").write_text('{"a": 1}\n{not json}\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                store.read_jsonl(out / "bad.jsonl")


# ===================================================================== cli
class CliTests(unittest.TestCase):
    def test_dry_run_and_validate_and_recall(self):
        import build_policy_corpus as cli

        self.assertEqual(cli.main(["list-connectors"]), 0)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            rc = cli.main(["sweep", "--dry-run", "--connectors", "camara,senado,lexml,querido_diario,alesp,confaz", "--out", str(out), "--since", "2025-01-01", "--until", "2026-09-23"])
            self.assertEqual(rc, 0)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["run"]["dry_run"])
            self.assertGreater(manifest["fetcher"]["planned_requests"], 0)
            names = {c["connector"]: c for c in manifest["connectors"]}
            self.assertIn("skipped: unverified", names["confaz"]["notes"][0])
            self.assertEqual(manifest["counts"]["documents_unique"], 0)
            self.assertIsNotNone(manifest["recall"])
            self.assertEqual(cli.main(["recall", "--out", str(out)]), 0)
        self.assertEqual(cli.main(["validate-corpus", str(ROOT / "docs" / "phase3_redata_policy_corpus.json")]), 0)
        with self.assertRaises(SystemExit):
            cli.main(["sweep", "--since", "2026-12-31", "--until", "2026-01-01"])

    def test_dotenv_loader(self):
        import build_policy_corpus as cli
        import os

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / ".env"
            p.write_text('# comment\nINLABS_TEST_KEY="abc"\nBROKEN LINE\nEXISTING=new\n', encoding="utf-8")
            os.environ["EXISTING"] = "old"
            try:
                n = cli.load_dotenv(p)
                self.assertEqual(n, 1)
                self.assertEqual(os.environ["INLABS_TEST_KEY"], "abc")
                self.assertEqual(os.environ["EXISTING"], "old")
            finally:
                os.environ.pop("INLABS_TEST_KEY", None)
                os.environ.pop("EXISTING", None)


class CanonicalTests(unittest.TestCase):
    def test_type_aliases(self):
        from policy_corpus.models import canonical_type
        self.assertEqual(canonical_type("MPV"), "medida provisoria")
        self.assertEqual(canonical_type("Medida Provisória"), "medida provisoria")
        self.assertEqual(canonical_type("REN"), "resolucao")
        self.assertEqual(canonical_type("pl"), "pl", "bills are not folded into norms")

    def test_recall_matches_mpv_against_medida_provisoria(self):
        corpus = [Document(source="camara", layer=Layer.FEDERAL_BILL, jurisdiction="BR", doc_type="MPV", number="1318", year=2025, title="MPV", house="CD")]
        gold = [{"id": "MP", "title": "Medida Provisoria No. 1.318/2025 - REDATA", "type": "government_legal_text", "category": "c", "urn": "urn:lex:br:federal:medida.provisoria:2025-09-17;1318"}]
        rep = recall.compute_recall(gold, corpus)
        self.assertEqual(rep["hits"], 1)
        self.assertEqual(rep["hit_detail"][0]["matched_by"], "type_number_year")
        k = recall.gold_key({"title": "Resolucao Normativa ANEEL No. 1.122/2025 - x", "type": "regulatory_resolution"})
        self.assertEqual((k["doc_type"], k["number"], k["year"]), ("resolucao", "1122", 2025))
        k2 = recall.gold_key({"title": "Mocao CONAMA No. 147/2026", "type": "regulatory_act"})
        self.assertEqual((k2["doc_type"], k2["number"]), ("mocao", "147"))

    def test_canonicalize_relations(self):
        lei = Document(source="lexml", layer=Layer.FEDERAL_NORM, jurisdiction="BR", doc_type="lei", number="15504", year=2026, title="L", date="2026-09-15")
        pl = Document(source="camara", layer=Layer.FEDERAL_BILL, jurisdiction="BR", doc_type="pl", number="278", year=2026, title="P", house="CD")
        rels = [
            Relation(pl.key, "urn:lex:br:federal:lei:2026;15504", RelationType.ENACTED_AS, "e", "senado"),
            Relation("gold:PL", "urn:lex:br:federal:lei:2026-09-15;15504", RelationType.ENACTED_AS, "e", "curated"),
            Relation("gold:PL", "urn:lex:br:federal:lei:2026;15504", RelationType.ENACTED_AS, "dup", "curated"),
            Relation("gold:UNKNOWN", "urn:lex:br:federal:lei:1999;1", RelationType.CITES, "e", "curated"),
            Relation(lei.key, "urn:lex:br:federal:lei:2026;15504", RelationType.CITES, "self", "x"),
        ]
        out = linking.canonicalize_relations(rels, [lei, pl], {"gold:PL": pl.key})
        self.assertEqual(len(out), 2, "three ENACTED_AS collapse to one; self-loop dropped; unresolved kept")
        self.assertEqual((out[0].src, out[0].dst), (pl.key, lei.key))
        self.assertEqual((out[1].src, out[1].dst), ("gold:UNKNOWN", "urn:lex:br:federal:lei:1999;1"))
        self.assertEqual(linking.urn_family("urn:lex:br:federal:lei:2026-09-15;15504"), "urn:lex:br:federal:lei:2026;15504")
        self.assertIsNone(linking.urn_family(None))


class RequestShapeLadderTests(unittest.TestCase):
    """The first live run (2026-09-24) got HTTP 400 from both APIs for the
    fullest request shape; the connectors must degrade and say why."""

    def test_camara_degrades_on_400_and_logs_api_message(self):
        bad = (400, b'{"status": 400, "message": "Par\u00e2metro dataApresentacaoFim inv\u00e1lido"}', JSON_H)
        routes = [
            (lambda m, u, p, b: u.endswith("/proposicoes") and "dataApresentacaoFim" in p, bad),
            (lambda m, u, p, b: u.endswith("/proposicoes") and p.get("keywords") == "data center" and "dataApresentacaoInicio" in p, (200, fixture("camara_list_page1.json"), JSON_H)),
            (lambda m, u, p, b: "pagina=2" in u, (200, fixture("camara_list_page2.json"), JSON_H)),
            (lambda m, u, p, b: u.endswith("/proposicoes"), (200, b'{"dados": [], "links": []}', JSON_H)),
            (lambda m, u, p, b: "/proposicoes/" in u, (200, b'{"dados": {"statusProposicao": {"descricaoSituacao": "x"}}}', JSON_H)),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            f = StubFetcher(routes, Path(tmp))
            res = camara.sweep(make_ctx(f, since=dt.date(2024, 1, 1), until=dt.date(2026, 9, 24), query_terms=("data center",)))
        self.assertEqual(res.errors, [], res.errors)
        self.assertTrue(any("shape 'start_only' accepted" in n and "dataApresentacaoFim inv" in n for n in res.notes), res.notes)
        # per-year retry of the full window was attempted before degrading
        year_calls = [c for c in f.calls if (c["params"] or {}).get("dataApresentacaoFim") == "2024-12-31"]
        self.assertEqual(len(year_calls), 1)
        self.assertIn("PL 278/2026", {d.title for d in res.documents})

    def test_camara_client_side_window_filter_when_dates_dropped(self):
        routes = [
            (lambda m, u, p, b: u.endswith("/proposicoes") and ("dataApresentacaoInicio" in p or "dataApresentacaoFim" in p), (400, b"bad", HTML_H)),
            (lambda m, u, p, b: u.endswith("/proposicoes") and p.get("keywords") == "data center", (200, fixture("camara_list_page1.json"), JSON_H)),
            (lambda m, u, p, b: "pagina=2" in u, (200, fixture("camara_list_page2.json"), JSON_H)),
            (lambda m, u, p, b: u.endswith("/proposicoes"), (200, b'{"dados": [], "links": []}', JSON_H)),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            f = StubFetcher(routes, Path(tmp))
            res = camara.sweep(make_ctx(f, since=dt.date(2026, 1, 1), until=dt.date(2026, 9, 24), detail=False))
        titles = {d.title for d in res.documents}
        self.assertIn("PL 278/2026", titles)
        self.assertNotIn("MPV 1318/2025", titles, "2025 row filtered client-side once dates left the request")
        self.assertTrue(any("shape 'no_dates' accepted" in n for n in res.notes), res.notes)

    def test_camara_all_shapes_rejected_is_one_error(self):
        routes = [(lambda m, u, p, b: True, (400, b'{"message": "nope"}', JSON_H))]
        with tempfile.TemporaryDirectory() as tmp:
            res = camara.sweep(make_ctx(StubFetcher(routes, Path(tmp)), query_terms=("redata",)))
        self.assertEqual(len(res.errors), 1)
        self.assertIn("every request shape rejected", res.errors[0])
        self.assertIn("keywords_only: HTTP 400: nope", res.errors[0])

    def test_senado_degrades_to_termo_per_year(self):
        routes = [
            (lambda m, u, p, b: "dataInicioApresentacao" in p, (400, b'{"detail": "intervalo de datas inv\u00e1lido"}', JSON_H)),
            (lambda m, u, p, b: p.get("termo") == "data center" and p.get("ano") == 2026, (200, fixture("senado_processo_termo.json"), JSON_H)),
            (lambda m, u, p, b: p.get("termo") and "ano" in p, (200, b"[]", JSON_H)),
            (lambda m, u, p, b: "tipoNorma" in p, (200, b"[]", JSON_H)),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            f = StubFetcher(routes, Path(tmp))
            res = senado.sweep(make_ctx(f, since=dt.date(2024, 1, 1), until=dt.date(2026, 9, 24)))
        self.assertEqual(res.errors, [], res.errors)
        self.assertTrue(any("shape 'termo_per_year' accepted" in n and "intervalo de datas" in n for n in res.notes), res.notes)
        self.assertIn("br:cd:pl:278:2026", {d.key for d in res.documents})
        self.assertEqual(sorted(c["params"]["ano"] for c in f.calls if "ano" in (c["params"] or {}) and c["params"].get("termo") == "data center"), [2024, 2025, 2026])

    def test_error_detail_formats(self):
        r = FetchResult("u", 400, b'{"message": "Par\u00e2metro x"}', JSON_H, "t")
        self.assertEqual(r.error_detail(), "HTTP 400: Par\u00e2metro x")
        self.assertEqual(FetchResult("u", 500, b"<html>Internal   error</html>", HTML_H, "t").error_detail(), "HTTP 500: <html>Internal error</html>")
        self.assertEqual(FetchResult("u", 503, b"", {}, "t").error_detail(), "HTTP 503 (empty body)")
        self.assertEqual(FetchResult("u", 0, b"", {}, "t", error="ConnectionError: x").error_detail(), "ConnectionError: x")


if __name__ == "__main__":
    unittest.main(verbosity=2)
