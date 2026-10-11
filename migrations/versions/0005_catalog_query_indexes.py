"""Indexes for bounded canonical and evidence API queries."""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_variant_gene_gene_variant", "variant_gene", ["gene_id", "variant_id"]),
    ("ix_association_variant_page", "association", ["variant_id", "id"]),
    ("ix_association_trait_page", "association", ["trait_id", "id"]),
    ("ix_quality_issue_run_page", "quality_issue", ["ingestion_run_id", "id"]),
    ("ix_quality_issue_processing", "quality_issue", ["processing_observation_id"]),
    ("ix_quality_issue_enrichment", "quality_issue", ["enrichment_observation_id"]),
    ("ix_processing_association", "processing_observation", ["association_id"]),
)


def upgrade():
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)


def downgrade():
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
