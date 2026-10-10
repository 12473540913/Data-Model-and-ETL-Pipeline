"""initial canonical schema

Revision ID: 0001
Revises:
Create Date: 2026-10-10 09:22:24.621738

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "gene",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ensembl_gene_id", sa.String(length=40), nullable=True),
        sa.Column("symbol", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("chromosome", sa.String(length=30), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ensembl_gene_id"),
    )
    op.create_index(op.f("ix_gene_symbol"), "gene", ["symbol"], unique=False)
    op.create_table(
        "population",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("ancestry_label", sa.String(length=200), nullable=True),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_population_label"), "population", ["label"], unique=False)
    op.create_table(
        "source_system",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "study",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("accession", sa.String(length=80), nullable=True),
        sa.Column("pubmed_id", sa.String(length=30), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("author", sa.String(length=300), nullable=True),
        sa.Column("publication_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sample_description", sa.Text(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_study_accession"), "study", ["accession"], unique=True)
    op.create_index(op.f("ix_study_pubmed_id"), "study", ["pubmed_id"], unique=False)
    op.create_table(
        "trait",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ontology_id", sa.String(length=100), nullable=True),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ontology_id"),
    )
    op.create_index(op.f("ix_trait_name"), "trait", ["name"], unique=False)
    op.create_table(
        "variant",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("rsid", sa.String(length=50), nullable=True),
        sa.Column("assembly", sa.String(length=30), nullable=False),
        sa.Column("chromosome", sa.String(length=30), nullable=True),
        sa.Column("position", sa.BigInteger(), nullable=True),
        sa.Column("reference_allele", sa.Text(), nullable=True),
        sa.Column("alternate_alleles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("variant_class", sa.String(length=80), nullable=True),
        sa.Column("most_severe_consequence", sa.String(length=150), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_variant_chromosome"), "variant", ["chromosome"], unique=False)
    op.create_index(
        "ix_variant_locus", "variant", ["assembly", "chromosome", "position"], unique=False
    )
    op.create_index(op.f("ix_variant_rsid"), "variant", ["rsid"], unique=True)
    op.create_table(
        "association",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("trait_id", sa.UUID(), nullable=False),
        sa.Column("study_id", sa.UUID(), nullable=True),
        sa.Column("population_id", sa.UUID(), nullable=True),
        sa.Column("p_value", sa.Numeric(precision=50, scale=40), nullable=True),
        sa.Column("effect_allele", sa.Text(), nullable=True),
        sa.Column("other_allele", sa.Text(), nullable=True),
        sa.Column("effect_size", sa.Numeric(precision=30, scale=12), nullable=True),
        sa.Column("effect_size_type", sa.String(length=80), nullable=True),
        sa.Column("confidence_interval", sa.String(length=200), nullable=True),
        sa.Column("sample_size", sa.BigInteger(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["population_id"],
            ["population.id"],
        ),
        sa.ForeignKeyConstraint(
            ["study_id"],
            ["study.id"],
        ),
        sa.ForeignKeyConstraint(
            ["trait_id"],
            ["trait.id"],
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["variant.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_association_study_id"), "association", ["study_id"], unique=False)
    op.create_index(op.f("ix_association_trait_id"), "association", ["trait_id"], unique=False)
    op.create_index(op.f("ix_association_variant_id"), "association", ["variant_id"], unique=False)
    op.create_table(
        "dataset_release",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_system_id", sa.UUID(), nullable=False),
        sa.Column("release_key", sa.String(length=200), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_system_id"],
            ["source_system.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_system_id", "release_key"),
    )
    op.create_table(
        "study_population",
        sa.Column("study_id", sa.UUID(), nullable=False),
        sa.Column("population_id", sa.UUID(), nullable=False),
        sa.Column("stage", sa.String(length=50), nullable=False),
        sa.Column("sample_size", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["population_id"],
            ["population.id"],
        ),
        sa.ForeignKeyConstraint(
            ["study_id"],
            ["study.id"],
        ),
        sa.PrimaryKeyConstraint("study_id", "population_id", "stage"),
    )
    op.create_table(
        "trait_synonym",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("trait_id", sa.UUID(), nullable=False),
        sa.Column("source_system_id", sa.UUID(), nullable=True),
        sa.Column("synonym", sa.String(length=300), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_system_id"],
            ["source_system.id"],
        ),
        sa.ForeignKeyConstraint(
            ["trait_id"],
            ["trait.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trait_id", "source_system_id", "synonym"),
    )
    op.create_table(
        "variant_alias",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("source_system_id", sa.UUID(), nullable=True),
        sa.Column("alias_type", sa.String(length=50), nullable=False),
        sa.Column("alias_value", sa.String(length=500), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_system_id"],
            ["source_system.id"],
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["variant.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_system_id", "alias_type", "alias_value"),
    )
    op.create_table(
        "variant_gene",
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("gene_id", sa.UUID(), nullable=False),
        sa.Column("relationship_type", sa.String(length=80), nullable=False),
        sa.Column("source_system_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["gene_id"],
            ["gene.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_system_id"],
            ["source_system.id"],
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["variant.id"],
        ),
        sa.PrimaryKeyConstraint("variant_id", "gene_id", "relationship_type"),
    )
    op.create_table(
        "ingestion_run",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_system_id", sa.UUID(), nullable=False),
        sa.Column("dataset_release_id", sa.UUID(), nullable=True),
        sa.Column("pipeline_name", sa.String(length=200), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_release_id"],
            ["dataset_release.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_system_id"],
            ["source_system.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "quality_issue",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ingestion_run_id", sa.UUID(), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("entity_key", sa.String(length=300), nullable=True),
        sa.Column("rule_code", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=30), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_run.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "raw_object",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ingestion_run_id", sa.UUID(), nullable=False),
        sa.Column("source_record_type", sa.String(length=100), nullable=False),
        sa.Column("source_record_id", sa.String(length=300), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_run.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "association_source",
        sa.Column("association_id", sa.UUID(), nullable=False),
        sa.Column("source_system_id", sa.UUID(), nullable=False),
        sa.Column("source_record_id", sa.String(length=300), nullable=False),
        sa.Column("ingestion_run_id", sa.UUID(), nullable=False),
        sa.Column("raw_object_id", sa.UUID(), nullable=True),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["association_id"],
            ["association.id"],
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_run.id"],
        ),
        sa.ForeignKeyConstraint(
            ["raw_object_id"],
            ["raw_object.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_system_id"],
            ["source_system.id"],
        ),
        sa.PrimaryKeyConstraint("association_id", "source_system_id", "source_record_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("association_source")
    op.drop_table("raw_object")
    op.drop_table("quality_issue")
    op.drop_table("ingestion_run")
    op.drop_table("variant_gene")
    op.drop_table("variant_alias")
    op.drop_table("trait_synonym")
    op.drop_table("study_population")
    op.drop_table("dataset_release")
    op.drop_index(op.f("ix_association_variant_id"), table_name="association")
    op.drop_index(op.f("ix_association_trait_id"), table_name="association")
    op.drop_index(op.f("ix_association_study_id"), table_name="association")
    op.drop_table("association")
    op.drop_index(op.f("ix_variant_rsid"), table_name="variant")
    op.drop_index("ix_variant_locus", table_name="variant")
    op.drop_index(op.f("ix_variant_chromosome"), table_name="variant")
    op.drop_table("variant")
    op.drop_index(op.f("ix_trait_name"), table_name="trait")
    op.drop_table("trait")
    op.drop_index(op.f("ix_study_pubmed_id"), table_name="study")
    op.drop_index(op.f("ix_study_accession"), table_name="study")
    op.drop_table("study")
    op.drop_table("source_system")
    op.drop_index(op.f("ix_population_label"), table_name="population")
    op.drop_table("population")
    op.drop_index(op.f("ix_gene_symbol"), table_name="gene")
    op.drop_table("gene")
