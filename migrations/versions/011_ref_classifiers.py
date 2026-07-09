"""Фаза 3: классификаторы Решения КТС № 378 (справочник для граф и ФЛК F-09).

Одна таблица ref_classifiers: (classifier_code, item_code) → name_ru.
Наполнение — scripts/seed/classifiers.py (консервативный набор: значения,
не сверенные с актуальной редакцией Решения 378, помечены verified=false).

Revision ID: 011
Revises: 010
Create Date: 2026-07-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ref_classifiers",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "classifier_code", sa.String(40), nullable=False,
            comment="delivery_terms | transport_types | customs_procedures | "
                    "mos_methods | deal_nature | doc_kinds | payment_kinds | ...",
        ),
        sa.Column("item_code", sa.String(20), nullable=False),
        sa.Column("name_ru", sa.Text, nullable=False),
        sa.Column(
            "verified", sa.Boolean, nullable=False, server_default=sa.text("false"),
            comment="false = сверить с актуальной редакцией Решения 378",
        ),
        sa.Column("valid_from", sa.Date, nullable=True),
        sa.Column("valid_to", sa.Date, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("classifier_code", "item_code",
                            name="uq_ref_classifier_item"),
    )
    op.create_index("idx_ref_classifiers_code", "ref_classifiers", ["classifier_code"])


def downgrade() -> None:
    op.drop_index("idx_ref_classifiers_code", table_name="ref_classifiers")
    op.drop_table("ref_classifiers")
