from genomic_platform.config import get_settings
from genomic_platform.connectors.base import SourceConnector
from genomic_platform.connectors.http import ResilientHttpClient

class NCBIVariationConnector(SourceConnector):
    source_code = "NCBI_DBSNP"
    def __init__(self):
        s = get_settings(); self.http = ResilientHttpClient(s.ncbi_variation_base_url, s.http_timeout_seconds)
    async def healthcheck(self) -> bool:
        return True
    async def refsnp(self, rsid: str) -> dict:
        numeric = rsid.lower().removeprefix("rs")
        return (await self.http.get(f"/beta/refsnp/{numeric}")).json()
