from typing import Any, AsyncIterator
from genomic_platform.config import get_settings
from genomic_platform.connectors.base import SourceConnector
from genomic_platform.connectors.http import ResilientHttpClient

class GWASCatalogConnector(SourceConnector):
    source_code = "GWAS_CATALOG"
    def __init__(self):
        s = get_settings(); self.http = ResilientHttpClient(s.gwas_base_url, s.http_timeout_seconds)
    async def healthcheck(self) -> bool:
        try: await self.http.get("/metadata"); return True
        except Exception: return False
    async def metadata(self) -> dict[str, Any]:
        return (await self.http.get("/metadata")).json()
    async def discover_traits(self, query: str) -> list[dict[str, Any]]:
        r = await self.http.get("/efo_traits", params={"search": query})
        payload = r.json()
        if isinstance(payload, list): return payload
        if isinstance(payload, dict):
            for key in ("results", "content"):
                if isinstance(payload.get(key), list): return payload[key]
            embedded = payload.get("_embedded")
            if isinstance(embedded, dict):
                for value in embedded.values():
                    if isinstance(value, list): return value
        return []
    async def iter_associations_for_trait(self, efo_id: str, show_child_traits: bool = True, page_size: int = 100) -> AsyncIterator[dict[str, Any]]:
        path = "/associations"
        params = {"efo_trait": efo_id, "show_child_traits": str(show_child_traits).lower(), "size": page_size}
        while path:
            payload = (await self.http.get(path, params=params)).json(); params = None
            records = []
            if isinstance(payload, list): records = payload
            elif isinstance(payload, dict):
                if isinstance(payload.get("content"), list): records = payload["content"]
                elif isinstance(payload.get("results"), list): records = payload["results"]
                elif isinstance(payload.get("_embedded"), dict):
                    for value in payload["_embedded"].values():
                        if isinstance(value, list): records = value; break
            for record in records: yield record
            path = None
            if isinstance(payload, dict):
                links = payload.get("_links") or payload.get("links") or {}
                nxt = links.get("next") if isinstance(links, dict) else None
                if isinstance(nxt, dict): nxt = nxt.get("href")
                if isinstance(nxt, str) and nxt: path = nxt
