"""test_debt: sổ nợ test (bề mặt mới chưa có test) do khâu dò nợ ghi

`kind` cố ý KHÔNG có CHECK (Phase 3 dùng chung bảng). Chỉ được có MỘT dòng đang mở cho mỗi (project, kind, surface):
partial unique index `WHERE closed_at IS NULL` (cú pháp có ở cả PostgreSQL lẫn SQLite).

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_debt",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("surface", sa.String(length=1024), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_reason", sa.String(length=16), nullable=True),
        sa.Column("pr_url", sa.String(length=512), nullable=True),
        sa.Column("opened_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_seen_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("closed_reason IS NULL OR closed_reason IN ('covered','surface_gone','ignored')",
                           name=op.f("ck_test_debt_closed_reason_valid")),
        sa.CheckConstraint("(closed_at IS NULL) = (closed_reason IS NULL)", name=op.f("ck_test_debt_closed_pair")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_test_debt_project_id_projects"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["opened_job_id"], ["jobs.id"], name=op.f("fk_test_debt_opened_job_id_jobs"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["last_seen_job_id"], ["jobs.id"], name=op.f("fk_test_debt_last_seen_job_id_jobs"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_debt")),
    )
    op.create_index("uq_test_debt_open", "test_debt", ["project_id", "kind", "surface"], unique=True,
                    postgresql_where=sa.text("closed_at IS NULL"), sqlite_where=sa.text("closed_at IS NULL"))


def downgrade() -> None:
    op.drop_index("uq_test_debt_open", table_name="test_debt")
    op.drop_table("test_debt")
