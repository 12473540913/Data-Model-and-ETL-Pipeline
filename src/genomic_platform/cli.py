import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import typer
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from genomic_platform.config import get_settings
from genomic_platform.db import SessionLocal
from genomic_platform.domain.models import SourceSystem
from genomic_platform.etl.ingestion import IngestionError, ingest_gwas, replay_gwas, resume_gwas
from genomic_platform.etl.pipelines import (
    discover_associations_by_trait,
    discover_associations_by_variant,
    discover_traits,
    ingest_gwas_eye_color,
)
from genomic_platform.schema_migrations import upgrade_database

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)


@app.command("init-db")
def init_db():
    upgrade_database()
    typer.echo("Database migrations applied.")


@app.command()
def seed():
    upgrade_database()
    rows = [
        ("GWAS_CATALOG", "NHGRI-EBI GWAS Catalog", "https://www.ebi.ac.uk/gwas/"),
        ("ENSEMBL", "Ensembl", "https://www.ensembl.org/"),
        ("NCBI_DBSNP", "NCBI dbSNP / Variation", "https://www.ncbi.nlm.nih.gov/snp/"),
        ("EFO", "Experimental Factor Ontology", "https://www.ebi.ac.uk/efo/"),
        ("OPEN_TARGETS", "Open Targets", "https://platform.opentargets.org/"),
    ]
    with SessionLocal() as s:
        for code, name, url in rows:
            if not s.query(SourceSystem).filter_by(code=code).first():
                s.add(SourceSystem(code=code, name=name, base_url=url))
        s.commit()
    typer.echo("Seeded source systems.")


@app.command("discover-traits")
def discover(
    search_terms: Annotated[
        list[str], typer.Argument(help='One or more trait queries, e.g. "lactase persistence".')
    ],
):
    typer.echo(json.dumps(asyncio.run(discover_traits(search_terms)), indent=2, default=str))


@app.command("discover-associations-by-trait")
def discover_associations_trait(efo_id: str = typer.Argument(..., help="EFO trait ID to query.")):
    typer.echo(
        json.dumps(asyncio.run(discover_associations_by_trait(efo_id)), indent=2, default=str)
    )


@app.command("discover-associations-by-variant")
def discover_associations_variant(rsid: str = typer.Argument(..., help="Variant rsID to query.")):
    typer.echo(
        json.dumps(asyncio.run(discover_associations_by_variant(rsid)), indent=2, default=str)
    )


@app.command("ingest-gwas-eye-color")
def ingest(execute: bool = typer.Option(False, "--execute")):
    typer.echo(
        json.dumps(asyncio.run(ingest_gwas_eye_color(dry_run=not execute)), indent=2, default=str)
    )


def _run_ingestion(operation: Callable[..., dict[str, Any]], **kwargs: Any) -> None:
    db_engine = None
    try:
        db_engine = create_engine(
            get_settings().database_url,
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args={"connect_timeout": 10},
        )
        summary = operation(db_engine, **kwargs)
        typer.echo(json.dumps(summary, indent=2))
    except IngestionError as error:
        if error.summary is not None:
            typer.echo(json.dumps(error.summary, indent=2))
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1) from None
    except SQLAlchemyError:
        typer.echo(
            "Error: PostgreSQL operation failed. Check database availability and migrations; credentials are not displayed.",
            err=True,
        )
        raise typer.Exit(1) from None
    except (OSError, UnicodeError, ValueError):
        typer.echo(
            "Error: Cannot read valid GWAS input or configuration. Check the input file and settings.",
            err=True,
        )
        raise typer.Exit(1) from None
    finally:
        if db_engine is not None:
            db_engine.dispose()


@app.command("ingest-gwas-trait")
def ingest_trait(
    efo_id: Annotated[str, typer.Argument(help="Ontology ID, e.g. EFO_0010507.")],
    batch_size: Annotated[
        int, typer.Option(min=1, max=10000, help="Records per atomic batch.")
    ] = 500,
    page_size: Annotated[int, typer.Option(min=1, max=1000)] = 100,
    timeout: Annotated[float, typer.Option(min=0.1, max=300, help="HTTP timeout in seconds.")] = 30,
    show_child_traits: Annotated[bool, typer.Option(help="Include child ontology traits.")] = True,
):
    """Fetch, preserve and load GWAS associations by EFO ID."""
    _run_ingestion(
        ingest_gwas,
        mode="trait",
        identifier=efo_id,
        batch_size=batch_size,
        page_size=page_size,
        timeout=timeout,
        show_child_traits=show_child_traits,
    )


@app.command("ingest-gwas-variant")
def ingest_variant(
    rsid: Annotated[
        str, typer.Argument(help="Variant rsID, e.g. rs4988235. Traits are not inferred.")
    ],
    batch_size: Annotated[int, typer.Option(min=1, max=10000)] = 500,
    page_size: Annotated[int, typer.Option(min=1, max=1000)] = 100,
    timeout: Annotated[float, typer.Option(min=0.1, max=300)] = 30,
):
    """Fetch, preserve and load all returned variant associations."""
    _run_ingestion(
        ingest_gwas,
        mode="variant",
        identifier=rsid,
        batch_size=batch_size,
        page_size=page_size,
        timeout=timeout,
    )


@app.command("ingest-gwas-file")
def ingest_file(
    path: Annotated[
        Path,
        typer.Argument(
            exists=True, dir_okay=False, readable=True, help="Saved GWAS association JSON array."
        ),
    ],
    batch_size: Annotated[int, typer.Option(min=1, max=10000)] = 500,
):
    """Load saved JSON without contacting GWAS."""
    _run_ingestion(ingest_gwas, mode="file", path=path, batch_size=batch_size)


@app.command("replay-gwas")
def replay(
    run_id: Annotated[UUID, typer.Argument(help="Original ingestion or replay run UUID.")],
    batch_size: Annotated[int, typer.Option(min=1, max=10000)] = 500,
):
    """Verify checksums and process original raw observations in a new linked run."""
    _run_ingestion(replay_gwas, origin=run_id, batch_size=batch_size)


@app.command("resume-gwas")
def resume(
    run_id: Annotated[
        UUID, typer.Argument(help="Failed or interrupted CLI ingestion/replay run UUID.")
    ],
):
    """Resume a run idempotently; committed batches are not processed twice."""
    _run_ingestion(resume_gwas, run_id=run_id)


if __name__ == "__main__":
    app()
