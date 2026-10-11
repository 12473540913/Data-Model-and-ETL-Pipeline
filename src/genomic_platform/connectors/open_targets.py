import httpx

from genomic_platform.config import get_settings
from genomic_platform.connectors.base import SourceConnector


class OpenTargetsConnector(SourceConnector):
    source_code = "OPEN_TARGETS"

    def __init__(self):
        self.url = get_settings().open_targets_graphql_url

    async def healthcheck(self) -> bool:
        async with httpx.AsyncClient(timeout=20) as c:
            return (await c.post(self.url, json={"query": "query { __typename }"})).is_success

    async def query(self, graphql: str, variables: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(self.url, json={"query": graphql, "variables": variables or {}})
            r.raise_for_status()
            return r.json()
