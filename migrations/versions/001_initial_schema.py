"""Initial schema with pgvector

Revision ID: 001
Revises: None
Create Date: 2026-02-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "tnved_tree",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("code_formatted", sa.String(14)),
        sa.Column("level", sa.Integer, nullable=False),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey("tnved_tree.id")),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("name_full", sa.Text),
        sa.Column("section_roman", sa.String(5)),
        sa.Column("group_code", sa.String(2)),
        sa.Column("duty_rate", sa.String(200)),
        sa.Column("vat_rate", sa.String(20)),
        sa.Column("excise", sa.String(200)),
        sa.Column("unit_primary", sa.String(50)),
        sa.Column("unit_secondary", sa.String(50)),
        sa.Column("is_leaf", sa.Boolean, default=False),
        sa.Column("valid_from", sa.Date),
        sa.Column("valid_to", sa.Date),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_tnved_code", "tnved_tree", ["code"])
    op.create_index("idx_tnved_parent", "tnved_tree", ["parent_id"])
    op.create_index("idx_tnved_level", "tnved_tree", ["level"])
    op.create_index("idx_tnved_section", "tnved_tree", ["section_roman"])
    op.create_index("idx_tnved_group", "tnved_tree", ["group_code"])

    op.create_table(
        "tnved_notes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("scope_type", sa.String(10), nullable=False),
        sa.Column("scope_code", sa.String(10), nullable=False),
        sa.Column("note_number", sa.String(10)),
        sa.Column("note_type", sa.String(20), nullable=False),
        sa.Column("note_text", sa.Text, nullable=False),
        sa.Column("references_to", ARRAY(sa.Text)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_notes_scope", "tnved_notes", ["scope_type", "scope_code"])
    op.create_index("idx_notes_type", "tnved_notes", ["note_type"])

    op.execute("""
        CREATE TABLE tnved_explanatory_notes (
            id SERIAL PRIMARY KEY,
            code VARCHAR(10) NOT NULL,
            code_level INTEGER NOT NULL,
            section_type VARCHAR(20),
            content TEXT NOT NULL,
            chunk_index INTEGER,
            chunk_text TEXT,
            embedding vector(1024),
            source_file VARCHAR(100),
            page_number INTEGER,
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.create_index("idx_expl_code", "tnved_explanatory_notes", ["code"])
    op.create_index("idx_expl_level", "tnved_explanatory_notes", ["code_level"])
    # HNSW index for vector search
    op.execute(
        "CREATE INDEX idx_expl_embedding ON tnved_explanatory_notes "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "tnved_opi",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("rule_number", sa.String(5), nullable=False, unique=True),
        sa.Column("rule_text", sa.Text, nullable=False),
        sa.Column("prompt_instruction", sa.Text, nullable=False),
        sa.Column("applicable_levels", ARRAY(sa.Integer)),
        sa.Column("priority", sa.Integer),
    )

    op.create_table(
        "classification_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.String(36), unique=True, nullable=False),
        sa.Column("state", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_session_id", "classification_sessions", ["session_id"])


def downgrade() -> None:
    op.drop_table("classification_sessions")
    op.drop_table("tnved_opi")
    op.drop_table("tnved_explanatory_notes")
    op.drop_table("tnved_notes")
    op.drop_table("tnved_tree")
    op.execute("DROP EXTENSION IF EXISTS vector")
