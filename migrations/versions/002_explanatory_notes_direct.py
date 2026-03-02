"""Replace RAG chunking with direct section-based explanatory notes.

Drop chunk_index, chunk_text, embedding columns and HNSW index.
Add group_code, title, content_tokens columns.

Revision ID: 002
Revises: 001
Create Date: 2026-03-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop HNSW vector index first
    op.drop_index("idx_expl_embedding", table_name="tnved_explanatory_notes")

    # Drop RAG-specific columns
    op.drop_column("tnved_explanatory_notes", "embedding")
    op.drop_column("tnved_explanatory_notes", "chunk_index")
    op.drop_column("tnved_explanatory_notes", "chunk_text")

    # Add new columns for direct loading
    op.add_column(
        "tnved_explanatory_notes",
        sa.Column("group_code", sa.String(2), nullable=False, server_default=""),
    )
    op.add_column(
        "tnved_explanatory_notes",
        sa.Column("title", sa.Text, nullable=True),
    )
    op.add_column(
        "tnved_explanatory_notes",
        sa.Column("content_tokens", sa.Integer, nullable=True),
    )

    op.create_index("idx_expl_group", "tnved_explanatory_notes", ["group_code"])


def downgrade() -> None:
    op.drop_index("idx_expl_group", table_name="tnved_explanatory_notes")

    op.drop_column("tnved_explanatory_notes", "content_tokens")
    op.drop_column("tnved_explanatory_notes", "title")
    op.drop_column("tnved_explanatory_notes", "group_code")

    op.add_column(
        "tnved_explanatory_notes",
        sa.Column("chunk_index", sa.Integer, nullable=True),
    )
    op.add_column(
        "tnved_explanatory_notes",
        sa.Column("chunk_text", sa.Text, nullable=True),
    )
    op.execute(
        "ALTER TABLE tnved_explanatory_notes ADD COLUMN embedding vector(1024)"
    )
    op.execute(
        "CREATE INDEX idx_expl_embedding ON tnved_explanatory_notes "
        "USING hnsw (embedding vector_cosine_ops)"
    )
