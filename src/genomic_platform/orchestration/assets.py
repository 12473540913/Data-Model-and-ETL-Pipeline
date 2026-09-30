# Optional Dagster adapter. Keep orchestration out of domain/ETL logic.
try:
    from dagster import asset
    from genomic_platform.etl.pipelines import ingest_gwas_eye_color
    @asset
    async def gwas_eye_color_associations():
        return await ingest_gwas_eye_color(dry_run=False)
except ImportError:
    pass
