"""Add ocr_jobs table + FK classification_jobs.parent_ocr_job_id → ocr_jobs.id.

Depends on migration 008 (classification_jobs), which created the
`parent_ocr_job_id` column without a FK — the OCR module simply
landed after it. This migration backfills the constraint.

Structure: one OcrJob represents a single user upload of 1..N files.
All files are OCR'd, their raw text preserved, then a single LLM
"structure" step turns concatenated text into ProductCard[] ready
for the cascade. Chain to classification is optional and tracked
via the (already existing) parent_ocr_job_id back-reference.

Revision ID: 007
Revises: 008
Create Date: 2026-04-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "007"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "ocr_jobs",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "status", sa.String(32), nullable=False,
            comment="pending | extracting | structuring | completed | failed | cancelled",
        ),
        sa.Column(
            "stage", sa.String(32), nullable=True,
            comment="Sub-stage label for UI progress (mirrors status but finer-grained)",
        ),
        # Input
        sa.Column("files", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"),
                  comment="[{filename, size_bytes, sha256, content_type, stored_path}]"),
        sa.Column("auto_classify", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        # Extract stage — raw text per file
        sa.Column("extraction", JSONB, nullable=True,
                  comment="[{filename, text, processing_time_s, provider_task_id, warning}]"),
        # Structure stage — LLM output
        sa.Column("products", JSONB, nullable=True,
                  comment="[{ProductCard...}] with source_ref = {file_index, line_hint}"),
        sa.Column("skipped_notes", JSONB, nullable=True,
                  comment="LLM's notes about discarded lines (services, empty rows)"),
        # Chain
        sa.Column("linked_classification_job_id", UUID(as_uuid=True), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        # Audit
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_ocr_jobs_status", "ocr_jobs", ["status"])
    op.create_index("idx_ocr_jobs_expires", "ocr_jobs", ["expires_at"])
    op.create_index(
        "idx_ocr_jobs_linked_classify", "ocr_jobs", ["linked_classification_job_id"]
    )

    # Backfill FK on classification_jobs (column itself already exists from 008).
    op.create_foreign_key(
        "fk_classification_jobs_parent_ocr",
        source_table="classification_jobs",
        referent_table="ocr_jobs",
        local_cols=["parent_ocr_job_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_classification_jobs_parent_ocr",
        "classification_jobs",
        type_="foreignkey",
    )
    op.drop_index("idx_ocr_jobs_linked_classify", table_name="ocr_jobs")
    op.drop_index("idx_ocr_jobs_expires", table_name="ocr_jobs")
    op.drop_index("idx_ocr_jobs_status", table_name="ocr_jobs")
    op.drop_table("ocr_jobs")
