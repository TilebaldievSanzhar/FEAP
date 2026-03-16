"""Drop verification_context from ntm_measures.

This column is redundant — product_description in ntm_measure_codes
serves the same purpose for LLM verification of dual matching.

Revision ID: 006
Revises: 005
Create Date: 2026-03-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("ntm_measures", "verification_context")


def downgrade() -> None:
    op.add_column(
        "ntm_measures",
        sa.Column(
            "verification_context", sa.Text, nullable=True,
            comment="Brief description for LLM verification",
        ),
    )
