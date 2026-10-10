"""Exact numerics, stable source observations, and per-run provenance."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("association", "p_value", type_=sa.Numeric(), existing_type=sa.Numeric(50, 40))
    op.alter_column(
        "association", "effect_size", type_=sa.Numeric(), existing_type=sa.Numeric(30, 12)
    )
    op.add_column("association", sa.Column("identity_key", sa.String(64)))
    op.add_column("association", sa.Column("p_value_text", sa.Text()))
    op.add_column(
        "association",
        sa.Column("p_value_status", sa.String(30), nullable=False, server_default="missing"),
    )
    op.execute("UPDATE association SET p_value_status = 'reported' WHERE p_value IS NOT NULL")
    op.create_unique_constraint("uq_association_identity", "association", ["identity_key"])
    op.create_check_constraint(
        "ck_association_p_value", "association", "p_value IS NULL OR (p_value > 0 AND p_value <= 1)"
    )
    op.create_check_constraint(
        "ck_association_p_value_status",
        "association",
        "p_value_status IN ('reported', 'missing', 'unreconciled')",
    )
    op.create_check_constraint(
        "ck_association_unreconciled",
        "association",
        "p_value_status != 'unreconciled' OR p_value IS NULL",
    )
    op.create_check_constraint(
        "ck_association_p_value_consistency",
        "association",
        "(p_value_status = 'reported' AND p_value IS NOT NULL) OR "
        "(p_value_status != 'reported' AND p_value IS NULL)",
    )
    op.alter_column("variant", "assembly", existing_type=sa.String(30), nullable=True)

    op.create_unique_constraint(
        "uq_ingestion_run_source", "ingestion_run", ["id", "source_system_id"]
    )
    op.create_index(
        "ix_ingestion_run_source_started", "ingestion_run", ["source_system_id", "started_at"]
    )
    op.create_check_constraint(
        "ck_ingestion_run_status",
        "ingestion_run",
        "status IN ('running', 'completed', 'completed_with_issues', 'failed')",
    )

    for column in [
        sa.Column("payload_text", sa.Text()),
        sa.Column("normalized_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("association_id", sa.UUID()),
        sa.Column("disposition", sa.String(30), nullable=False, server_default="captured"),
        sa.Column("p_value_candidate", sa.Numeric()),
        sa.Column("p_value_text", sa.Text()),
        sa.Column("p_value_mantissa", sa.Text()),
        sa.Column("p_value_exponent", sa.Text()),
    ]:
        op.add_column("raw_object", column)
    op.create_foreign_key(
        "fk_raw_object_association", "raw_object", "association", ["association_id"], ["id"]
    )
    op.create_unique_constraint("uq_raw_object_run", "raw_object", ["id", "ingestion_run_id"])
    op.create_unique_constraint(
        "uq_raw_object_association_source",
        "raw_object",
        ["id", "ingestion_run_id", "association_id", "source_record_id"],
    )
    op.create_unique_constraint(
        "uq_raw_object_run_payload",
        "raw_object",
        ["ingestion_run_id", "source_record_type", "sha256"],
    )
    op.create_index("ix_raw_object_association_id", "raw_object", ["association_id"])
    op.create_index(
        "ix_raw_object_source_record", "raw_object", ["source_record_type", "source_record_id"]
    )
    op.create_index(
        "ix_raw_object_run_disposition", "raw_object", ["ingestion_run_id", "disposition"]
    )
    op.create_check_constraint(
        "ck_raw_object_disposition",
        "raw_object",
        "disposition IN ('captured', 'loaded', 'partial', 'quarantined')",
    )

    op.create_unique_constraint(
        "uq_association_source_record",
        "association_source",
        ["source_system_id", "source_record_id"],
    )
    op.create_index("ix_association_source_raw", "association_source", ["raw_object_id"])
    op.drop_constraint(
        "association_source_raw_object_id_fkey", "association_source", type_="foreignkey"
    )
    op.execute("""
        UPDATE raw_object r SET association_id = s.association_id
        FROM association_source s
        WHERE s.raw_object_id = r.id;
    """)
    op.create_foreign_key(
        "fk_association_source_raw_run",
        "association_source",
        "raw_object",
        ["raw_object_id", "ingestion_run_id", "association_id", "source_record_id"],
        ["id", "ingestion_run_id", "association_id", "source_record_id"],
    )
    op.create_foreign_key(
        "fk_association_source_run_source",
        "association_source",
        "ingestion_run",
        ["ingestion_run_id", "source_system_id"],
        ["id", "source_system_id"],
    )

    op.add_column("quality_issue", sa.Column("raw_object_id", sa.UUID()))
    op.create_index("ix_quality_issue_raw_object_id", "quality_issue", ["raw_object_id"])
    op.create_unique_constraint(
        "uq_quality_issue_raw_rule", "quality_issue", ["raw_object_id", "rule_code"]
    )
    op.create_foreign_key(
        "fk_quality_issue_raw_run",
        "quality_issue",
        "raw_object",
        ["raw_object_id", "ingestion_run_id"],
        ["id", "ingestion_run_id"],
    )
    op.create_check_constraint(
        "ck_quality_issue_severity", "quality_issue", "severity IN ('warning', 'error')"
    )


def downgrade():
    # Never silently discard captured evidence or round scientific values.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM raw_object WHERE payload_text IS NOT NULL)
               OR EXISTS (SELECT 1 FROM variant WHERE assembly IS NULL)
               OR EXISTS (
                   SELECT 1 FROM association
                   WHERE p_value != round(p_value, 40)
                      OR effect_size != round(effect_size, 12)
                      OR abs(effect_size) >= 1e18
               )
            THEN RAISE EXCEPTION 'Downgrade would discard provenance or numeric precision; export and reconcile data first';
            END IF;
        END $$;
    """)
    op.drop_constraint("fk_quality_issue_raw_run", "quality_issue", type_="foreignkey")
    op.drop_constraint("uq_quality_issue_raw_rule", "quality_issue", type_="unique")
    op.drop_constraint("ck_quality_issue_severity", "quality_issue", type_="check")
    op.drop_index("ix_quality_issue_raw_object_id", table_name="quality_issue")
    op.drop_column("quality_issue", "raw_object_id")
    op.drop_constraint("fk_association_source_raw_run", "association_source", type_="foreignkey")
    op.drop_constraint("fk_association_source_run_source", "association_source", type_="foreignkey")
    op.create_foreign_key(
        "association_source_raw_object_id_fkey",
        "association_source",
        "raw_object",
        ["raw_object_id"],
        ["id"],
    )
    op.drop_constraint("uq_association_source_record", "association_source", type_="unique")
    op.drop_index("ix_association_source_raw", table_name="association_source")
    op.drop_constraint("fk_raw_object_association", "raw_object", type_="foreignkey")
    op.drop_constraint("uq_raw_object_run_payload", "raw_object", type_="unique")
    op.drop_constraint("uq_raw_object_association_source", "raw_object", type_="unique")
    op.drop_constraint("uq_raw_object_run", "raw_object", type_="unique")
    op.drop_constraint("ck_raw_object_disposition", "raw_object", type_="check")
    for index in [
        "ix_raw_object_association_id",
        "ix_raw_object_source_record",
        "ix_raw_object_run_disposition",
    ]:
        op.drop_index(index, table_name="raw_object")
    for column in [
        "payload_text",
        "normalized_json",
        "association_id",
        "disposition",
        "p_value_candidate",
        "p_value_text",
        "p_value_mantissa",
        "p_value_exponent",
    ]:
        op.drop_column("raw_object", column)
    op.drop_constraint("uq_ingestion_run_source", "ingestion_run", type_="unique")
    op.drop_constraint("ck_ingestion_run_status", "ingestion_run", type_="check")
    op.drop_index("ix_ingestion_run_source_started", table_name="ingestion_run")
    for name in [
        "ck_association_p_value",
        "ck_association_p_value_status",
        "ck_association_unreconciled",
        "ck_association_p_value_consistency",
    ]:
        op.drop_constraint(name, "association", type_="check")
    op.drop_constraint("uq_association_identity", "association", type_="unique")
    for column in ["identity_key", "p_value_text", "p_value_status"]:
        op.drop_column("association", column)
    op.alter_column("association", "p_value", type_=sa.Numeric(50, 40), existing_type=sa.Numeric())
    op.alter_column(
        "association", "effect_size", type_=sa.Numeric(30, 12), existing_type=sa.Numeric()
    )
    op.alter_column("variant", "assembly", existing_type=sa.String(30), nullable=False)
