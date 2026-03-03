"""Add vat_exemptions table for VAT import tax management.

Stores VAT exemptions by ТН ВЭД code prefix with validity dates.
Supports Kyrgyz Republic tax code (НК КР, ст. 289).

Revision ID: 003
Revises: 002
Create Date: 2026-03-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vat_exemptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "code_pattern",
            sa.String(10),
            nullable=False,
            comment="Code or prefix, e.g. '3004' matches all 3004*",
        ),
        sa.Column(
            "exemption_type",
            sa.String(10),
            nullable=False,
            comment="full | reduced",
        ),
        sa.Column(
            "rate_override",
            sa.String(20),
            nullable=True,
            comment="e.g. '0%' for full exemption",
        ),
        sa.Column("legal_ref", sa.Text, nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("valid_from", sa.Date, nullable=True),
        sa.Column("valid_to", sa.Date, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_vat_exemption_code_pattern",
        "vat_exemptions",
        ["code_pattern"],
    )


def downgrade() -> None:
    op.drop_index("idx_vat_exemption_code_pattern", table_name="vat_exemptions")
    op.drop_table("vat_exemptions")
