"""Separate immutable acquisition from bounded processing and replay."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_raw_object_source_run", "raw_object", ["id", "ingestion_run_id", "source_record_id"]
    )
    op.drop_constraint("fk_association_source_raw_run", "association_source", type_="foreignkey")
    op.create_foreign_key(
        "fk_association_source_raw_run",
        "association_source",
        "raw_object",
        ["raw_object_id", "ingestion_run_id", "source_record_id"],
        ["id", "ingestion_run_id", "source_record_id"],
    )
    op.add_column(
        "ingestion_run",
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column("ingestion_run", sa.Column("originating_run_id", sa.UUID()))
    op.create_foreign_key(
        "fk_ingestion_run_origin", "ingestion_run", "ingestion_run", ["originating_run_id"], ["id"]
    )
    op.create_index("ix_ingestion_run_originating_run_id", "ingestion_run", ["originating_run_id"])
    op.create_table(
        "processing_observation",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("ingestion_run_id", sa.UUID(), sa.ForeignKey("ingestion_run.id"), nullable=False),
        sa.Column("raw_object_id", sa.UUID(), sa.ForeignKey("raw_object.id"), nullable=False),
        sa.Column("association_id", sa.UUID(), sa.ForeignKey("association.id")),
        sa.Column("disposition", sa.String(30), nullable=False),
        sa.Column("normalized_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ingestion_run_id", "raw_object_id", name="uq_processing_run_raw"),
        sa.UniqueConstraint("id", "ingestion_run_id", name="uq_processing_id_run"),
        sa.CheckConstraint(
            "disposition IN ('loaded', 'partial', 'quarantined')", name="ck_processing_disposition"
        ),
    )
    op.create_index(
        "ix_processing_observation_ingestion_run_id", "processing_observation", ["ingestion_run_id"]
    )
    op.create_index(
        "ix_processing_observation_raw_object_id", "processing_observation", ["raw_object_id"]
    )
    op.create_table(
        "ingestion_batch",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("ingestion_run_id", sa.UUID(), sa.ForeignKey("ingestion_run.id"), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("phase", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(), nullable=False),
        sa.Column("checkpoint_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ingestion_run_id", "sequence", name="uq_ingestion_batch_sequence"),
        sa.CheckConstraint("phase IN ('capture', 'processing')", name="ck_ingestion_batch_phase"),
        sa.CheckConstraint("status IN ('completed', 'failed')", name="ck_ingestion_batch_status"),
    )
    op.create_index("ix_ingestion_batch_ingestion_run_id", "ingestion_batch", ["ingestion_run_id"])
    op.add_column("quality_issue", sa.Column("processing_observation_id", sa.UUID()))
    op.create_foreign_key(
        "fk_quality_issue_processing_run",
        "quality_issue",
        "processing_observation",
        ["processing_observation_id", "ingestion_run_id"],
        ["id", "ingestion_run_id"],
    )
    op.create_unique_constraint(
        "uq_quality_issue_processing_rule",
        "quality_issue",
        ["processing_observation_id", "rule_code"],
    )


def downgrade():
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM processing_observation)
             OR EXISTS (SELECT 1 FROM ingestion_batch)
             OR EXISTS (SELECT 1 FROM ingestion_run WHERE originating_run_id IS NOT NULL OR metadata_json != '{}'::jsonb)
          THEN RAISE EXCEPTION 'Downgrade would discard processing history; export it first';
          END IF;
        END $$;
    """)
    op.drop_constraint("fk_quality_issue_processing_run", "quality_issue", type_="foreignkey")
    op.drop_constraint("uq_quality_issue_processing_rule", "quality_issue", type_="unique")
    op.drop_column("quality_issue", "processing_observation_id")
    op.drop_table("ingestion_batch")
    op.drop_table("processing_observation")
    op.drop_index("ix_ingestion_run_originating_run_id", table_name="ingestion_run")
    op.drop_constraint("fk_ingestion_run_origin", "ingestion_run", type_="foreignkey")
    op.drop_column("ingestion_run", "originating_run_id")
    op.drop_column("ingestion_run", "metadata_json")
    op.drop_constraint("fk_association_source_raw_run", "association_source", type_="foreignkey")
    op.create_foreign_key(
        "fk_association_source_raw_run",
        "association_source",
        "raw_object",
        ["raw_object_id", "ingestion_run_id", "association_id", "source_record_id"],
        ["id", "ingestion_run_id", "association_id", "source_record_id"],
    )
    op.drop_constraint("uq_raw_object_source_run", "raw_object", type_="unique")
