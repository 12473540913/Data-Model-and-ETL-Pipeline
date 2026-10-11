from abc import ABC, abstractmethod


class SourceConnector(ABC):
    source_code: str
    @abstractmethod
    async def healthcheck(self) -> bool: ...
