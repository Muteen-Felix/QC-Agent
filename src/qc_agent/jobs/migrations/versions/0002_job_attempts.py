"""job attempts counter (requeue job bị mất executor có giới hạn)

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("attempts", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("jobs", "attempts")
