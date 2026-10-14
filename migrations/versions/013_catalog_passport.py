"""Паспорт товара в каталоге: параметры партии и обоснование кода.

product_catalog.passport (JSONB) — calc.schemas.PassportIn: параметры расчёта
(страна, производитель, стоимость, базис, расходы, решения по мерам) и
цепочка классификации. Суммы не хранятся: паспорт пересчитывается на дату
по актуальным ставкам (POST /calc, GET /catalog/{id}/passport.pdf).

Revision ID: 013
Revises: 012
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "product_catalog",
        sa.Column(
            "passport", postgresql.JSONB(), nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="Паспорт товара: параметры партии и обоснование кода (calc.schemas.PassportIn)",
        ),
    )


def downgrade() -> None:
    op.drop_column("product_catalog", "passport")
