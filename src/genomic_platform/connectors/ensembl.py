from genomic_platform.config import get_settings
from genomic_platform.connectors.base import SourceConnector
from genomic_platform.connectors.http import ResilientHttpClient

class EnsemblConnector(SourceConnector):
    source_code = "ENSEMBL"
    def __init__(self):
        s = get_settings(); self.http = ResilientHttpClient(s.ensembl_base_url, s.http_timeout_seconds, headers={"Accept":"application/json","Content-Type":"application/json","User-Agent":"GenomicDataIntegrationPlatform/0.1"})
    async def healthcheck(self) -> bool:
        try: await self.http.get("/info/ping"); return True
        except Exception: return False
    async def variation(self, rsid: str, populations: bool = False) -> dict:
        return (await self.http.get(f"/variation/homo_sapiens/{rsid}", params={"pops": 1 if populations else 0})).json()
    async def variations_batch(self, rsids: list[str]) -> dict:
        if len(rsids) > 200: raise ValueError("Ensembl variation POST accepts at most 200 IDs per request.")
        return (await self.http.post("/variation/homo_sapiens", json={"ids": rsids})).json()
