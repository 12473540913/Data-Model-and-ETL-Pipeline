import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential


def transient_http_error(error: BaseException) -> bool:
    return isinstance(error, httpx.TransportError) or (
        isinstance(error, httpx.HTTPStatusError)
        and (error.response.status_code == 429 or error.response.status_code >= 500)
    )


class ResilientHttpClient:
    def __init__(self, base_url: str, timeout: float = 30.0, headers: dict | None = None):
        self.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers=headers or {"User-Agent": "GenomicDataIntegrationPlatform/0.1"},
            follow_redirects=True,
        )

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        retry=retry_if_exception(transient_http_error),
        reraise=True,
    )
    async def get(self, path: str, **kwargs):
        r = await self.client.get(path, **kwargs)
        r.raise_for_status()
        return r

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        retry=retry_if_exception(transient_http_error),
        reraise=True,
    )
    async def post(self, path: str, **kwargs):
        r = await self.client.post(path, **kwargs)
        r.raise_for_status()
        return r
