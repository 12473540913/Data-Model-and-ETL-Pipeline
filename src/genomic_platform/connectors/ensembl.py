import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from tenacity import RetryCallState, wait_exponential

from genomic_platform.config import get_settings
from genomic_platform.connectors.base import SourceConnector
from genomic_platform.connectors.http import ResilientHttpClient
from genomic_platform.etl.normalize import normalize_rsid

logger = logging.getLogger(__name__)


def ensembl_retry_wait(state: RetryCallState) -> float:
    delay = float(wait_exponential(multiplier=0.5, min=0.5, max=8)(state))
    error = state.outcome.exception() if state.outcome is not None else None
    if isinstance(error, httpx.HTTPStatusError):
        value = error.response.headers.get("Retry-After")
        if value:
            try:
                seconds = float(value)
            except ValueError:
                try:
                    seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    logger.warning("Ensembl returned an invalid Retry-After header.")
                    return delay
            if 0 <= seconds < float("inf"):
                # Very long server cooldowns are surfaced rather than retried early.
                if seconds > 300:
                    raise error
                delay = max(delay, seconds)
    return delay


class EnsemblConnector(SourceConnector):
    source_code = "ENSEMBL"

    def __init__(
        self,
        assembly: str = "GRCh38",
        timeout: float | None = None,
        requests_per_second: float = 5,
    ):
        if assembly not in ("GRCh37", "GRCh38"):
            raise ValueError("Ensembl assembly must be GRCh37 or GRCh38.")
        if not 0 < requests_per_second <= 15:
            raise ValueError("Ensembl request rate must be greater than zero and at most 15.")
        settings = get_settings()
        self.assembly = assembly
        self.base_url = (
            settings.ensembl_base_url if assembly == "GRCh38" else settings.ensembl_grch37_base_url
        )
        self.http = ResilientHttpClient(
            self.base_url,
            timeout if timeout is not None else settings.http_timeout_seconds,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "GenomicDataIntegrationPlatform/0.1",
            },
            request_interval=1 / requests_per_second,
        )

    async def healthcheck(self) -> bool:
        try:
            await self.http.get("/info/ping")
            return True
        except httpx.HTTPError as error:
            logger.warning("Ensembl healthcheck failed (%s).", type(error).__name__)
            return False

    async def lookup_response(self, rsid: str) -> httpx.Response:
        canonical = normalize_rsid(rsid)
        if canonical is None or len(canonical) > 50:
            raise ValueError("Ensembl lookup requires a valid rsID.")
        try:
            return await self.http.get.retry_with(wait=ensembl_retry_wait)(
                self.http, f"/variation/homo_sapiens/{canonical}", params={"pops": 0}
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (400, 404):
                return error.response
            raise

    async def variation(self, rsid: str, populations: bool = False) -> dict:
        canonical = normalize_rsid(rsid)
        if canonical is None:
            raise ValueError("Ensembl lookup requires a valid rsID.")
        response = await self.http.get(
            f"/variation/homo_sapiens/{canonical}", params={"pops": int(populations)}
        )
        return response.json()

    async def variations_batch(self, rsids: list[str]) -> dict:
        if len(rsids) > 200:
            raise ValueError("Ensembl variation POST accepts at most 200 IDs per request.")
        return (await self.http.post("/variation/homo_sapiens", json={"ids": rsids})).json()

    async def aclose(self) -> None:
        await self.http.client.aclose()
