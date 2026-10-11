from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://genomics:genomics@localhost:5432/genomics"
    gwas_base_url: str = "https://www.ebi.ac.uk/gwas/rest/api/v2"
    ensembl_base_url: str = "https://rest.ensembl.org"
    ensembl_grch37_base_url: str = "https://grch37.rest.ensembl.org"
    ncbi_variation_base_url: str = "https://api.ncbi.nlm.nih.gov/variation/v0"
    open_targets_graphql_url: str = "https://api.platform.opentargets.org/api/v4/graphql"
    http_timeout_seconds: float = 30.0
    raw_data_dir: Path = Path("data/raw")
    quarantine_dir: Path = Path("data/quarantine")
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
