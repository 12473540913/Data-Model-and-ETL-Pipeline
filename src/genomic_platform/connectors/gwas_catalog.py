import hashlib
import logging
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from genomic_platform.config import get_settings
from genomic_platform.connectors.base import SourceConnector
from genomic_platform.connectors.http import ResilientHttpClient
from genomic_platform.etl.source_json import (
    SourcePayloadError,
    association_records,
    reject_constant,
    unique_object,
)


class GWASCatalogConnector(SourceConnector):
    source_code = "GWAS_CATALOG"

    def __init__(self, timeout: float | None = None):
        s = get_settings()
        self.http = ResilientHttpClient(s.gwas_base_url, timeout or s.http_timeout_seconds)
        self.base_url = s.gwas_base_url
        self.request_metadata: dict[str, Any] = {}

    async def healthcheck(self) -> bool:
        try:
            await self.http.get("/metadata")
            return True
        except httpx.HTTPError as error:
            logging.getLogger(__name__).warning(
                "GWAS healthcheck failed (%s).", type(error).__name__
            )
            return False

    async def metadata(self) -> dict[str, Any]:
        return (await self.http.get("/metadata")).json()

    async def discover_traits(self, query: str) -> list[dict[str, Any]]:
        r = await self.http.get("/efo-traits", params={"efo_trait": query})
        payload = r.json()
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("results", "content"):
                if isinstance(payload.get(key), list):
                    return payload[key]
            embedded = payload.get("_embedded")
            if isinstance(embedded, dict):
                for value in embedded.values():
                    if isinstance(value, list):
                        return value
        return []

    async def iter_associations_for_trait(
        self, efo_id: str, show_child_traits: bool = True, page_size: int = 100
    ) -> AsyncIterator[dict[str, Any]]:
        params = {
            "efo_trait": efo_id,
            "show_child_traits": str(show_child_traits).lower(),
            "size": page_size,
        }
        async for record in self._iter_associations(params):
            yield record

    async def iter_associations_for_variant(
        self, rsid: str, page_size: int = 100
    ) -> AsyncIterator[dict[str, Any]]:
        async for record in self._iter_associations({"rs_id": rsid, "size": page_size}):
            yield record

    async def _iter_associations(self, params: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        path = "/associations"
        visited = set()
        page = 0
        while path:
            if path in visited:
                raise SourcePayloadError("GWAS pagination contains a repeated page.")
            visited.add(path)
            page += 1
            self.request_metadata = {
                "path": path,
                "params": dict(params or {}),
                "page": page,
                "status_code": None,
            }
            response = await self.http.get(path, params=params)
            self.request_metadata["status_code"] = response.status_code
            if isinstance(response, httpx.Response):
                self.request_metadata["response_sha256"] = hashlib.sha256(
                    response.content
                ).hexdigest()
            payload = response.json(
                parse_float=Decimal, parse_constant=reject_constant, object_pairs_hook=unique_object
            )
            params = None
            records = association_records(payload)
            for record in records:
                yield record
            path = None
            if isinstance(payload, dict):
                links = payload.get("_links") or payload.get("links") or {}
                if not isinstance(links, dict):
                    raise SourcePayloadError("GWAS pagination links must be an object.")
                nxt = links.get("next") if isinstance(links, dict) else None
                if isinstance(nxt, dict):
                    if "href" not in nxt:
                        raise SourcePayloadError("GWAS next link is missing href.")
                    nxt = nxt["href"]
                if nxt is not None:
                    if not isinstance(nxt, str) or not nxt:
                        raise SourcePayloadError("GWAS pagination has an invalid next link.")
                    target = urlsplit(urljoin(self.base_url + "/", nxt))
                    base = urlsplit(self.base_url)
                    if (
                        target.scheme != base.scheme
                        or target.netloc != base.netloc
                        or target.username
                        or target.password
                    ):
                        raise SourcePayloadError(
                            "GWAS pagination points outside the configured source."
                        )
                    path = nxt

    async def aclose(self) -> None:
        await self.http.client.aclose()
