import asyncio, json, typer
from genomic_platform.db import Base, engine, SessionLocal
from genomic_platform.domain.models import SourceSystem
from genomic_platform.etl.pipelines import discover_eye_color_traits, ingest_gwas_eye_color
app=typer.Typer(no_args_is_help=True)

@app.command("init-db")
def init_db(): Base.metadata.create_all(engine); typer.echo("Database schema created.")

@app.command()
def seed():
    Base.metadata.create_all(engine)
    rows=[("GWAS_CATALOG","NHGRI-EBI GWAS Catalog","https://www.ebi.ac.uk/gwas/"),("ENSEMBL","Ensembl","https://www.ensembl.org/"),("NCBI_DBSNP","NCBI dbSNP / Variation","https://www.ncbi.nlm.nih.gov/snp/"),("EFO","Experimental Factor Ontology","https://www.ebi.ac.uk/efo/"),("OPEN_TARGETS","Open Targets","https://platform.opentargets.org/")]
    with SessionLocal() as s:
        for code,name,url in rows:
            if not s.query(SourceSystem).filter_by(code=code).first(): s.add(SourceSystem(code=code,name=name,base_url=url))
        s.commit()
    typer.echo("Seeded source systems.")

@app.command("discover-eye-color-traits")
def discover(): typer.echo(json.dumps(asyncio.run(discover_eye_color_traits()),indent=2,default=str))

@app.command("ingest-gwas-eye-color")
def ingest(execute: bool=typer.Option(False,"--execute")):
    typer.echo(json.dumps(asyncio.run(ingest_gwas_eye_color(dry_run=not execute)),indent=2,default=str))

if __name__ == "__main__": app()
