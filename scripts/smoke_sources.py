import asyncio
from genomic_platform.connectors.ensembl import EnsemblConnector
from genomic_platform.connectors.gwas_catalog import GWASCatalogConnector
from genomic_platform.connectors.open_targets import OpenTargetsConnector

async def main():
    for c in [GWASCatalogConnector(),EnsemblConnector(),OpenTargetsConnector()]:
        try: ok=await c.healthcheck()
        except Exception as exc: print(c.source_code,"ERROR",repr(exc))
        else: print(c.source_code,"OK" if ok else "FAILED")
if __name__ == "__main__": asyncio.run(main())
