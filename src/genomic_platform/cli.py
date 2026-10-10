import asyncio
import json

import typer

from genomic_platform.db import SessionLocal
from genomic_platform.domain.models import SourceSystem
from genomic_platform.etl.pipelines import (
    discover_associations_by_trait,
    discover_associations_by_variant,
    discover_traits,
    ingest_gwas_eye_color,
)
from genomic_platform.schema_migrations import upgrade_database

app=typer.Typer(no_args_is_help=True)

@app.command("init-db")
def init_db(): upgrade_database(); typer.echo("Database migrations applied.")

@app.command()
def seed():
    upgrade_database()
    rows=[("GWAS_CATALOG","NHGRI-EBI GWAS Catalog","https://www.ebi.ac.uk/gwas/"),("ENSEMBL","Ensembl","https://www.ensembl.org/"),("NCBI_DBSNP","NCBI dbSNP / Variation","https://www.ncbi.nlm.nih.gov/snp/"),("EFO","Experimental Factor Ontology","https://www.ebi.ac.uk/efo/"),("OPEN_TARGETS","Open Targets","https://platform.opentargets.org/")]
    with SessionLocal() as s:
        for code,name,url in rows:
            if not s.query(SourceSystem).filter_by(code=code).first(): s.add(SourceSystem(code=code,name=name,base_url=url))
        s.commit()
    typer.echo("Seeded source systems.")

@app.command("discover-traits")
def discover(search_terms: list[str]=typer.Argument(...,help="One or more trait queries, e.g. \"lactase persistence\".")):
    typer.echo(json.dumps(asyncio.run(discover_traits(search_terms)),indent=2,default=str))

@app.command("discover-associations-by-trait")
def discover_associations_trait(efo_id: str=typer.Argument(...,help="EFO trait ID to query.")):
    typer.echo(json.dumps(asyncio.run(discover_associations_by_trait(efo_id)),indent=2,default=str))

@app.command("discover-associations-by-variant")
def discover_associations_variant(rsid: str=typer.Argument(...,help="Variant rsID to query.")):
    typer.echo(json.dumps(asyncio.run(discover_associations_by_variant(rsid)),indent=2,default=str))

@app.command("ingest-gwas-eye-color")
def ingest(execute: bool=typer.Option(False,"--execute")):
    typer.echo(json.dumps(asyncio.run(ingest_gwas_eye_color(dry_run=not execute)),indent=2,default=str))

if __name__ == "__main__": app()
