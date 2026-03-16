"""Add non-tariff measures (NTM) tables.

Three tables for storing non-tariff regulation data:
- ntm_measure_types: 13 measure type categories (from TWS.BY R12 kinds)
- ntm_measures: specific measures (technical regulations, prohibitions, etc.)
- ntm_measure_codes: M:N link between measures and ТН ВЭД codes

Revision ID: 005
Revises: 004
Create Date: 2026-03-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Measure types (reference table) ──
    op.create_table(
        "ntm_measure_types",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "kind", sa.Integer, nullable=False, unique=True,
            comment="1-13, maps to TWS.BY R12 Kind values",
        ),
        sa.Column("name_ru", sa.Text, nullable=False),
        sa.Column("name_en", sa.Text, nullable=True),
        sa.Column(
            "category", sa.String(30), nullable=False,
            comment="prohibition|licensing|certification|sanitary|veterinary|phytosanitary|marking|quota|technical_regulation|other",
        ),
        sa.Column(
            "direction", sa.String(10), nullable=False,
            comment="import|export|both",
        ),
    )

    # ── Measures ──
    op.create_table(
        "ntm_measures",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "measure_type_id", sa.Integer,
            sa.ForeignKey("ntm_measure_types.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("legal_basis", sa.Text, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "verification_context", sa.Text, nullable=True,
            comment="Brief description for LLM verification: what the measure regulates, exclusions (100-300 words)",
        ),
        sa.Column("additional_info", sa.Text, nullable=True),
        sa.Column("valid_from", sa.Date, nullable=False),
        sa.Column("valid_to", sa.Date, nullable=True, comment="NULL = indefinite"),
        sa.Column(
            "scope", sa.String(10), nullable=False, server_default="eaeu",
            comment="eaeu|national",
        ),
        sa.Column(
            "country_code", sa.String(2), nullable=True,
            comment="NULL for EAEU-wide, 'KG' for Kyrgyzstan-specific",
        ),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column(
            "data_source", sa.String(30), nullable=True,
            comment="tws_by|tks_ru|manual|eec",
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_ntm_measures_type", "ntm_measures", ["measure_type_id"])
    op.create_index("idx_ntm_measures_valid", "ntm_measures", ["valid_from", "valid_to"])
    op.create_index("idx_ntm_measures_scope", "ntm_measures", ["scope", "country_code"])

    # ── Measure-code links (M:N) ──
    op.create_table(
        "ntm_measure_codes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "measure_id", sa.Integer,
            sa.ForeignKey("ntm_measures.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "code", sa.String(10), nullable=False,
            comment="ТН ВЭД code (4, 6, 8, or 10 digits)",
        ),
        sa.Column(
            "code_level", sa.Integer, nullable=False,
            comment="2=group, 3=position(4d), 4=subposition(6d), 5=leaf(8-10d)",
        ),
        sa.Column(
            "product_description", sa.Text, nullable=True,
            comment="Qualifier from NPA — not all products under this code are covered",
        ),
        sa.Column(
            "applies_to_children", sa.Boolean, nullable=False, server_default="true",
            comment="Whether this measure applies to all child codes",
        ),
        sa.Column("valid_from", sa.Date, nullable=True),
        sa.Column("valid_to", sa.Date, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_ntm_codes_code", "ntm_measure_codes", ["code"])
    op.create_index("idx_ntm_codes_measure", "ntm_measure_codes", ["measure_id"])
    op.create_unique_constraint(
        "uq_ntm_measure_code", "ntm_measure_codes", ["measure_id", "code"]
    )


def downgrade() -> None:
    op.drop_table("ntm_measure_codes")
    op.drop_table("ntm_measures")
    op.drop_table("ntm_measure_types")
