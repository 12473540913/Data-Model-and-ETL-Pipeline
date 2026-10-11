"""Preserve assembly-specific Ensembl evidence and conservative projections."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("uq_raw_object_run_payload", "raw_object", type_="unique")
    op.create_unique_constraint(
        "uq_raw_object_run_payload",
        "raw_object",
        ["ingestion_run_id", "source_record_type", "source_record_id", "sha256"],
        postgresql_nulls_not_distinct=True,
    )
    op.create_table(
        "variant_enrichment",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("variant_id", sa.UUID(), sa.ForeignKey("variant.id"), nullable=False),
        sa.Column(
            "raw_object_id", sa.UUID(), sa.ForeignKey("raw_object.id"), nullable=False, unique=True
        ),
        sa.Column("requested_assembly", sa.String(30), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("normalized_json", JSONB(), nullable=False),
        sa.UniqueConstraint(
            "variant_id", "requested_assembly", "sha256", name="uq_enrichment_evidence"
        ),
        sa.UniqueConstraint("id", "variant_id", name="uq_enrichment_variant"),
        sa.CheckConstraint(
            "requested_assembly IN ('GRCh37', 'GRCh38')", name="ck_enrichment_assembly"
        ),
    )
    op.create_index("ix_variant_enrichment_variant_id", "variant_enrichment", ["variant_id"])
    op.create_table(
        "variant_mapping",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("enrichment_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), sa.ForeignKey("variant.id"), nullable=False),
        sa.Column("ordinal", sa.BigInteger(), nullable=False),
        sa.Column("assembly", sa.Text()),
        sa.Column("coordinate_system", sa.Text()),
        sa.Column("chromosome", sa.Text()),
        sa.Column("position", sa.BigInteger()),
        sa.Column("end_position", sa.BigInteger()),
        sa.Column("strand", sa.BigInteger()),
        sa.Column("valid", sa.Boolean(), nullable=False),
        sa.Column("payload_json", JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["enrichment_id", "variant_id"],
            ["variant_enrichment.id", "variant_enrichment.variant_id"],
            name="fk_mapping_enrichment_variant",
        ),
        sa.UniqueConstraint("enrichment_id", "ordinal", name="uq_mapping_ordinal"),
        sa.UniqueConstraint(
            "id", "variant_id", "assembly", "chromosome", "position", name="uq_mapping_projection"
        ),
    )
    op.create_index("ix_variant_mapping_enrichment_id", "variant_mapping", ["enrichment_id"])
    op.create_index("ix_variant_mapping_assembly", "variant_mapping", ["variant_id", "assembly"])
    op.create_table(
        "enrichment_observation",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("ingestion_run_id", sa.UUID(), sa.ForeignKey("ingestion_run.id"), nullable=False),
        sa.Column("variant_id", sa.UUID(), sa.ForeignKey("variant.id"), nullable=False),
        sa.Column("enrichment_id", sa.UUID()),
        sa.Column("requested_assembly", sa.String(30), nullable=False),
        sa.Column("lookup_identifier", sa.String(50), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("metadata_json", JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["enrichment_id", "variant_id"],
            ["variant_enrichment.id", "variant_enrichment.variant_id"],
            name="fk_observation_enrichment_variant",
        ),
        sa.UniqueConstraint("ingestion_run_id", "variant_id", name="uq_enrichment_run_variant"),
        sa.UniqueConstraint("id", "ingestion_run_id", name="uq_enrichment_observation_run"),
        sa.CheckConstraint(
            "status IN ('enriched', 'partial', 'ambiguous', 'conflict', 'not_found', 'failed')",
            name="ck_enrichment_observation_status",
        ),
        sa.CheckConstraint(
            "requested_assembly IN ('GRCh37', 'GRCh38')", name="ck_observation_assembly"
        ),
    )
    op.create_index(
        "ix_enrichment_observation_ingestion_run_id", "enrichment_observation", ["ingestion_run_id"]
    )
    op.create_index(
        "ix_enrichment_selection",
        "enrichment_observation",
        ["variant_id", "requested_assembly", "status"],
    )
    op.add_column("quality_issue", sa.Column("enrichment_observation_id", sa.UUID()))
    op.create_foreign_key(
        "fk_quality_issue_enrichment_run",
        "quality_issue",
        "enrichment_observation",
        ["enrichment_observation_id", "ingestion_run_id"],
        ["id", "ingestion_run_id"],
    )
    op.create_unique_constraint(
        "uq_quality_issue_enrichment_rule",
        "quality_issue",
        ["enrichment_observation_id", "rule_code"],
    )
    op.add_column("variant", sa.Column("projection_mapping_id", sa.UUID()))
    op.create_check_constraint(
        "ck_variant_qualified_coordinates",
        "variant",
        "(chromosome IS NULL AND position IS NULL) OR assembly IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_variant_complete_projection",
        "variant",
        "projection_mapping_id IS NULL OR (assembly IS NOT NULL AND chromosome IS NOT NULL AND position IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_variant_projection_mapping",
        "variant",
        "variant_mapping",
        ["projection_mapping_id", "id", "assembly", "chromosome", "position"],
        ["id", "variant_id", "assembly", "chromosome", "position"],
    )


def downgrade():
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM variant_enrichment)
             OR EXISTS (SELECT 1 FROM enrichment_observation)
          THEN RAISE EXCEPTION 'Downgrade would discard enrichment evidence; export it first';
          END IF;
        END $$;
    """)
    op.drop_constraint("fk_variant_projection_mapping", "variant", type_="foreignkey")
    op.drop_constraint("ck_variant_complete_projection", "variant", type_="check")
    op.drop_constraint("ck_variant_qualified_coordinates", "variant", type_="check")
    op.drop_column("variant", "projection_mapping_id")
    op.drop_constraint("fk_quality_issue_enrichment_run", "quality_issue", type_="foreignkey")
    op.drop_constraint("uq_quality_issue_enrichment_rule", "quality_issue", type_="unique")
    op.drop_column("quality_issue", "enrichment_observation_id")
    op.drop_table("enrichment_observation")
    op.drop_table("variant_mapping")
    op.drop_table("variant_enrichment")
    op.drop_constraint("uq_raw_object_run_payload", "raw_object", type_="unique")
    op.create_unique_constraint(
        "uq_raw_object_run_payload",
        "raw_object",
        ["ingestion_run_id", "source_record_type", "sha256"],
    )
