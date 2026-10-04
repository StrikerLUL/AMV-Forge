"""HTTP-Client mit Rate-Limit, Wiederholungen und SQLite-Cache für alle öffentlichen APIs."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import httpx
from sqlalchemy.engine import Engine
from sqlmodel import Session

from backend.db.models import ApiCache

log = logging.getLogger(__name__)

# Diese Statuscodes sind echte Antworten ("gibt es nicht") und werden auch gecacht.
CACHEABLE_STATUS = {200, 404}
RETRY_STATUS = {429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    pass


class ApiClient:
    """Ein Client pro Dienst. Hält den Mindestabstand zwischen Anfragen ein und cacht Antworten."""

    def __init__(
        self,
        engine: Engine,
        service: str,
        base_url: str,
        min_interval: float,
        timeout: float = 30.0,
        max_retries: int = 4,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.engine = engine
        self.service = service
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.network_requests = 0
        self.cache_hits = 0
        self._last_request = 0.0
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": "AMV-Forge/0.2 (github.com/StrikerLUL/amv-forge)"},
        )

    def get_json(self, path: str, params: list[tuple[str, str]] | None = None) -> tuple[int, Any]:
        return self._request("GET", path, params=params)

    def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, Any]:
        return self._request("POST", path, payload=payload)

    def _cache_key(self, method: str, url: str, payload: dict[str, Any] | None) -> str:
        raw = f"{self.service}|{method}|{url}|{json.dumps(payload, sort_keys=True) if payload else ''}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _request(
        self,
        method: str,
        path: str,
        params: list[tuple[str, str]] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        url = str(self._http.build_request(method, path, params=params).url)
        key = self._cache_key(method, url, payload)

        with Session(self.engine) as session:
            cached = session.get(ApiCache, key)
            if cached is not None:
                self.cache_hits += 1
                return cached.status_code, json.loads(cached.body) if cached.body else None

        response = self._send_with_retries(method, path, params, payload)
        body: Any = None
        if response.content:
            try:
                body = response.json()
            except ValueError:
                body = None

        with Session(self.engine) as session:
            session.merge(
                ApiCache(
                    key=key,
                    service=self.service,
                    url=url,
                    status_code=response.status_code,
                    body=json.dumps(body) if body is not None else "",
                )
            )
            session.commit()
        return response.status_code, body

    def _wait_for_slot(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _send_with_retries(
        self,
        method: str,
        path: str,
        params: list[tuple[str, str]] | None,
        payload: dict[str, Any] | None,
    ) -> httpx.Response:
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            self._wait_for_slot()
            self.network_requests += 1
            try:
                response = self._http.request(method, path, params=params, json=payload)
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise ApiError(f"{self.service}: keine Verbindung ({exc})") from exc
                log.warning("%s: Verbindungsfehler, neuer Versuch in %.0f s", self.service, delay)
                time.sleep(delay)
                delay *= 2
                continue

            if response.status_code in CACHEABLE_STATUS:
                return response
            if response.status_code in RETRY_STATUS and attempt < self.max_retries:
                retry_after = response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
                log.warning("%s: HTTP %d, warte %.0f s", self.service, response.status_code, wait)
                time.sleep(wait)
                delay *= 2
                continue
            raise ApiError(f"{self.service}: HTTP {response.status_code} für {response.request.url}")
        raise ApiError(f"{self.service}: zu viele Fehlversuche")
