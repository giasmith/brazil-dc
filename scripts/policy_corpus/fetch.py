"""HTTP layer with on-disk caching, per-host throttling, retries, encoding
detection and provenance (bytes hash, retrieval time, HTTP status).

Why this exists instead of bare ``requests.get``:

* Planalto pages are windows-1252 with no charset header; DOU XML is UTF-8;
  state gazettes are whatever the OCR emitted. ``FetchResult.text`` applies a
  declared-charset -> strict UTF-8 -> cp1252 chain and records which one won,
  so a text-hash change can be attributed to the source, not the decoder.
* Every response is cached by (method, url, params, body). ``--offline`` runs
  the whole sweep from cache, which is how the test-suite and any machine
  without egress to ``*.leg.br`` exercise the pipeline.
* ``dry_run`` records what *would* be requested and returns empty bodies.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("policy_corpus.fetch")

USER_AGENT = (
    "SCN-PolicyCorpus/0.1 (+academic research; Sovereign Compute Nexus; "
    "contact via repository)"
)

_META_CHARSET = re.compile(rb"charset=[\"']?([A-Za-z0-9_\-]+)", re.IGNORECASE)
_XML_ENCODING = re.compile(rb"<\?xml[^>]*encoding=[\"']([A-Za-z0-9_\-]+)[\"']", re.IGNORECASE)


class OfflineMiss(RuntimeError):
    """Raised in offline mode when a request is not in the cache."""


@dataclasses.dataclass
class FetchResult:
    url: str
    status: int
    content: bytes
    headers: dict[str, str]
    retrieved_at: str
    from_cache: bool = False
    error: str | None = None
    _text: str | None = dataclasses.field(default=None, repr=False)
    encoding_used: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 and not self.error

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    def declared_charset(self) -> str | None:
        ctype = self.headers.get("content-type", "") or self.headers.get("Content-Type", "")
        m = re.search(r"charset=([A-Za-z0-9_\-]+)", ctype, re.IGNORECASE)
        if m:
            return m.group(1).lower()
        head = self.content[:4096]
        m2 = _XML_ENCODING.search(head) or _META_CHARSET.search(head)
        if m2:
            return m2.group(1).decode("ascii", "ignore").lower()
        return None

    @property
    def text(self) -> str:
        if self._text is not None:
            return self._text
        if not self.content:
            self._text = ""
            self.encoding_used = None
            return ""
        chain: list[str] = []
        declared = self.declared_charset()
        if declared:
            chain.append(declared)
        for enc in ("utf-8", "cp1252", "latin-1"):
            if enc not in chain:
                chain.append(enc)
        for enc in chain:
            try:
                self._text = self.content.decode(enc)
                self.encoding_used = enc
                return self._text
            except (UnicodeDecodeError, LookupError):
                continue
        self._text = self.content.decode("utf-8", errors="replace")
        self.encoding_used = "utf-8:replace"
        return self._text

    def json(self) -> Any:
        if not self.content:
            return None
        return json.loads(self.text)

    def error_detail(self, limit: int = 240) -> str:
        """Short human-readable reason for a failed request: the transport
        error, else the API's own message (Câmara/Senado return JSON with a
        ``message``/``detail``/``title``/``erro`` key), else the first bytes of
        the body. Always logged next to a 4xx so the cause is visible."""
        if self.error and self.error != "dry_run":
            return self.error
        if not self.content:
            return f"HTTP {self.status} (empty body)"
        try:
            payload = json.loads(self.text)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            for key in ("message", "detail", "title", "erro", "error", "mensagem"):
                val = payload.get(key)
                if val:
                    return f"HTTP {self.status}: {str(val)[:limit]}"
        snippet = re.sub(r"\s+", " ", self.text[:limit]).strip()
        return f"HTTP {self.status}: {snippet}" if snippet else f"HTTP {self.status}"


def _cache_key(method: str, url: str, params: dict[str, Any] | None, body: bytes | None) -> str:
    h = hashlib.sha256()
    h.update(method.upper().encode())
    h.update(b"\n")
    h.update(url.encode())
    h.update(b"\n")
    if params:
        # sort for stability; list values (repeated params) are kept in order
        h.update(json.dumps(params, sort_keys=True, ensure_ascii=False, default=str).encode())
    h.update(b"\n")
    if body:
        h.update(body)
    return h.hexdigest()


class Fetcher:
    def __init__(
        self,
        cache_dir: Path,
        offline: bool = False,
        dry_run: bool = False,
        timeout: float = 30.0,
        default_throttle: float = 0.5,
        host_throttle: dict[str, float] | None = None,
        max_retries: int = 4,
        refresh: bool = False,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.dry_run = dry_run
        self.timeout = timeout
        self.default_throttle = default_throttle
        self.host_throttle = host_throttle or {}
        self.refresh = refresh
        self.planned: list[dict[str, Any]] = []
        self.requests_made = 0
        self.cache_hits = 0
        self._last_call: dict[str, float] = {}
        self.session = requests.Session()
        retry = Retry(
            total=max_retries,
            connect=max_retries,
            read=max_retries,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({"User-Agent": USER_AGENT})

    # ------------------------------------------------------------------ cache
    def _paths(self, key: str) -> tuple[Path, Path]:
        return self.cache_dir / f"{key}.bin", self.cache_dir / f"{key}.json"

    def _load(self, key: str) -> FetchResult | None:
        bin_path, meta_path = self._paths(key)
        if not (bin_path.exists() and meta_path.exists()):
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            content = bin_path.read_bytes()
        except (OSError, json.JSONDecodeError) as exc:  # corrupt cache entry
            log.warning("cache entry %s unreadable (%s); ignoring", key[:12], exc)
            return None
        return FetchResult(
            url=meta["url"],
            status=int(meta["status"]),
            content=content,
            headers=meta.get("headers", {}),
            retrieved_at=meta["retrieved_at"],
            from_cache=True,
            error=meta.get("error"),
        )

    def _store(self, key: str, result: FetchResult) -> None:
        bin_path, meta_path = self._paths(key)
        try:
            bin_path.write_bytes(result.content)
            meta_path.write_text(
                json.dumps(
                    {
                        "url": result.url,
                        "status": result.status,
                        "headers": result.headers,
                        "retrieved_at": result.retrieved_at,
                        "error": result.error,
                        "sha256": result.sha256,
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("could not write cache entry %s: %s", key[:12], exc)

    # --------------------------------------------------------------- throttle
    def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        wait = self.host_throttle.get(host, self.default_throttle)
        last = self._last_call.get(host)
        now = time.monotonic()
        if last is not None and now - last < wait:
            time.sleep(wait - (now - last))
        self._last_call[host] = time.monotonic()

    # ---------------------------------------------------------------- request
    def request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: Any | None = None,
        data: dict[str, Any] | None = None,
        allow_cache: bool = True,
    ) -> FetchResult:
        body: bytes | None = None
        if json_body is not None:
            body = json.dumps(json_body, sort_keys=True, ensure_ascii=False).encode()
        elif data is not None:
            body = json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
        key = _cache_key(method, url, params, body)

        if self.dry_run:
            self.planned.append({"method": method, "url": url, "params": params})
            return FetchResult(url, 0, b"", {}, _now(), error="dry_run")

        if allow_cache and not self.refresh:
            cached = self._load(key)
            if cached is not None and (cached.ok or self.offline):
                self.cache_hits += 1
                return cached
        if self.offline:
            raise OfflineMiss(f"offline and not cached: {method} {url} {params or ''}")

        self._throttle(url)
        retrieved_at = _now()
        try:
            resp = self.session.request(
                method,
                url,
                params=params,
                headers=headers,
                json=json_body,
                data=data,
                timeout=self.timeout,
            )
            self.requests_made += 1
            result = FetchResult(
                url=resp.url,
                status=resp.status_code,
                content=resp.content,
                headers={k.lower(): v for k, v in resp.headers.items()},
                retrieved_at=retrieved_at,
            )
        except requests.RequestException as exc:
            self.requests_made += 1
            result = FetchResult(url, 0, b"", {}, retrieved_at, error=f"{type(exc).__name__}: {exc}")
        if allow_cache and (result.ok or result.status == 404):
            self._store(key, result)
        return result

    def get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, allow_cache: bool = True) -> FetchResult:
        return self.request("GET", url, params=params, headers=headers, allow_cache=allow_cache)

    def post(self, url: str, json_body: Any | None = None, data: dict[str, Any] | None = None, headers: dict[str, str] | None = None, allow_cache: bool = True) -> FetchResult:
        return self.request("POST", url, headers=headers, json_body=json_body, data=data, allow_cache=allow_cache)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
