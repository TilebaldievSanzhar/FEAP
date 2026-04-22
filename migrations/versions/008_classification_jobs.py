"""Add classification_jobs / classification_job_items / classification_job_events.

Async job system for batch classification. See docs/async_architecture.md.

- classification_jobs: one batch (or OCR-derived) classification job
- classification_job_items: one product within a job, links to a classification_session
  for needs_input flow
- classification_job_events: optional event stream (used for SSE / audit)

parent_ocr_job_id is a plain UUID column without FK because the OCR module
(docs/ocr_integration.md) lands in a separate migration. When ocr_jobs is
created, a follow-up migration can ALTER TABLE to add the FK.

Revision ID: 008
Revises: 006
Create Date: 2026-04-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "008"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ensure pgcrypto is available for gen_random_uuid().
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ── classification_jobs ───────────────────────────────────────────────────
    op.create_table(
        "classification_jobs",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "job_type", sa.String(32), nullable=False,
            comment="classify_batch | ocr | ocr_classify",
        ),
        sa.Column(
            "status", sa.String(32), nullable=False,
            comment="pending | running | completed | needs_input | partial | failed | cancelled",
        ),
        sa.Column(
            "params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("total_items", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completed_items", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_items", sa.Integer, nullable=False, server_default="0"),
        sa.Column("needs_input_items", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cancelled_items", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "parent_ocr_job_id", UUID(as_uuid=True), nullable=True,
            comment="No FK yet — ocr_jobs lands in a later migration",
        ),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=False,
            comment="created_at + JOB_RESULT_TTL_HOURS",
        ),
    )
    op.create_index("idx_jobs_status", "classification_jobs", ["status"])
    op.create_index("idx_jobs_expires", "classification_jobs", ["expires_at"])
    op.create_index("idx_jobs_parent_ocr", "classification_jobs", ["parent_ocr_job_id"])

    # ── classification_job_items ──────────────────────────────────────────────
    op.create_table(
        "classification_job_items",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "job_id", UUID(as_uuid=True),
            sa.ForeignKey("classification_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("product_description", sa.Text, nullable=False),
        sa.Column("product_card", JSONB, nullable=True),
        sa.Column("additional_info", JSONB, nullable=True),
        sa.Column("source_ref", JSONB, nullable=True),
        sa.Column(
            "status", sa.String(32), nullable=False,
            comment="pending | running | completed | needs_input | failed | cancelled",
        ),
        sa.Column(
            "classification_session_id", sa.String(36),
            sa.ForeignKey("classification_sessions.session_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("partial", JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
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
        sa.UniqueConstraint("job_id", "position", name="uq_job_item_position"),
    )
    op.create_index("idx_job_items_job", "classification_job_items", ["job_id"])
    op.create_index("idx_job_items_status", "classification_job_items", ["status"])
    op.create_index(
        "idx_job_items_session", "classification_job_items", ["classification_session_id"]
    )
    op.create_index(
        "idx_job_items_running_updated",
        "classification_job_items",
        ["status", "updated_at"],
    )

    # ── classification_job_events ─────────────────────────────────────────────
    op.create_table(
        "classification_job_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "job_id", UUID(as_uuid=True),
            sa.ForeignKey("classification_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id", UUID(as_uuid=True),
            sa.ForeignKey("classification_job_items.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "event_type", sa.String(32), nullable=False,
            comment="started | progress | item_done | item_needs_input | item_failed | finished | failed | cancelled",
        ),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_events_job_created",
        "classification_job_events",
        ["job_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("classification_job_events")
    op.drop_table("classification_job_items")
    op.drop_table("classification_jobs")
