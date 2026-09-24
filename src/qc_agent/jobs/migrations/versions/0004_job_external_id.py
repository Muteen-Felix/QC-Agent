"""jobs.external_id: CI ingest idempotent theo (project, external_id)

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("external_id", sa.String(length=128), nullable=True))
    op.create_unique_constraint("uq_jobs_project_external", "jobs", ["project_id", "external_id"])


def downgrade() -> None:
    op.drop_constraint("uq_jobs_project_external", "jobs", type_="unique")
    op.drop_column("jobs", "external_id")
