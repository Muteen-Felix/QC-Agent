"""Widen gate verdict and accept v2 values while preserving historical rows.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_jobs_gate_verdict_valid"), "jobs", type_="check")
    op.alter_column("jobs", "gate_verdict", existing_type=sa.String(length=8), type_=sa.String(length=24), existing_nullable=True)
    op.create_check_constraint(op.f("ck_jobs_gate_verdict_valid"), "jobs",
                               "gate_verdict IS NULL OR gate_verdict IN ('PASS','YELLOW','FAIL','PASSED','PASSED_WITH_WARNINGS','BLOCKED')")


def downgrade() -> None:
    op.execute("UPDATE jobs SET gate_verdict = CASE gate_verdict WHEN 'BLOCKED' THEN 'FAIL' "
               "WHEN 'PASSED_WITH_WARNINGS' THEN 'YELLOW' WHEN 'PASSED' THEN 'PASS' ELSE gate_verdict END")
    op.drop_constraint(op.f("ck_jobs_gate_verdict_valid"), "jobs", type_="check")
    op.alter_column("jobs", "gate_verdict", existing_type=sa.String(length=24), type_=sa.String(length=8), existing_nullable=True)
    op.create_check_constraint(op.f("ck_jobs_gate_verdict_valid"), "jobs",
                               "gate_verdict IS NULL OR gate_verdict IN ('PASS','YELLOW','FAIL')")
