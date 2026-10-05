"""Connector registry. Each connector exposes ``sweep(ctx) -> ConnectorResult``.

``verification`` records how much of each adapter was confirmed on 2026-09-23:

* ``live``          — endpoint shape confirmed against a real response.
* ``documentation`` — built from official docs / a standard protocol (SRU,
                      DRF) but not probed live (robots.txt or egress blocks).
* ``unverified``    — reverse-engineered or undocumented; runs only with
                      ``--include-unverified`` and every row it emits carries
                      ``verified_adapter=False`` and ``status=unverified`` so it
                      cannot reach Phase 3 without review.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from typing import Any, Callable

from ..fetch import Fetcher
from ..models import ConnectorResult


@dataclasses.dataclass
class Context:
    fetcher: Fetcher
    since: dt.date
    until: dt.date
    config: dict[str, Any]
    states: list[str]
    log: logging.Logger
    query_terms: tuple[str, ...]
    max_pages: int = 50
    detail: bool = True
    include_unverified: bool = False
    options: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class ConnectorSpec:
    name: str
    layer_hint: str
    verification: str  # live | documentation | unverified
    needs_credentials: bool
    run: Callable[[Context], ConnectorResult]
    description: str

    @property
    def gated(self) -> bool:
        return self.verification == "unverified"


def registry() -> dict[str, ConnectorSpec]:
    # Imported lazily so a syntax error in one adapter does not take down the CLI.
    from . import camara, senado, lexml, inlabs, confaz, querido_diario, sapl, states

    specs = [
        ConnectorSpec("camara", "federal_bill", "live", False, camara.sweep,
                      "Câmara dos Deputados Dados Abertos API v2 (/proposicoes?keywords=)"),
        ConnectorSpec("senado", "federal_bill", "live", False, senado.sweep,
                      "Senado Federal Dados Abertos /processo (termo=, tipoNorma/numeroNorma/anoNorma)"),
        ConnectorSpec("lexml", "federal_norm", "documentation", False, lexml.sweep,
                      "LexML Brasil SRU (CQL); live probe blocked by robots.txt on 2026-09-23"),
        ConnectorSpec("inlabs", "federal_regulatory", "documentation", True, inlabs.sweep,
                      "Imprensa Nacional INLABS daily DOU XML (needs INLABS_EMAIL/INLABS_PASSWORD)"),
        ConnectorSpec("querido_diario", "municipal_gazette", "documentation", False, querido_diario.sweep,
                      "Querido Diário public API (/gazettes); parameters from official docs"),
        ConnectorSpec("sapl", "municipal_bill", "documentation", False, sapl.sweep,
                      "Generic Interlegis SAPL REST adapter (/api/materia/materialegislativa/)"),
        ConnectorSpec("alesp", "state_bill", "documentation", False, states.sweep_alesp,
                      "ALESP bulk proposituras.zip (XML); path confirmed from a third-party monitor"),
        ConnectorSpec("confaz", "state_fiscal", "unverified", False, confaz.sweep,
                      "CONFAZ convênios ICMS listing + text (HTML scrape of a Plone site)"),
        ConnectorSpec("almg", "state_bill", "unverified", False, states.sweep_almg,
                      "ALMG web services; confirm against ALMG swagger before trusting"),
        ConnectorSpec("alrs", "state_bill", "unverified", False, states.sweep_alrs,
                      "AL-RS internal JSON API listaProposicaoCompleto (undocumented)"),
        ConnectorSpec("alece", "state_bill", "unverified", False, states.sweep_alece,
                      "ALECE has no bills API; stub that explains the manual path"),
    ]
    return {s.name: s for s in specs}
