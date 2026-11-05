"""Прецеденты классификации: предварительные решения таможенных органов ЕАЭС.

classification_precedents — запись «Сборника принятых предварительных решений
таможенных органов государств — членов ЕАЭС по классификации товаров» (ЕС 999,
справочник 1999 НСИ ЕАЭС): описание товара и десятизначный код, который ему
присвоил таможенный орган. Каскад ищет по описанию похожие решения и добавляет
их позиции к кандидатам L3 (feap.precedents, флаг CASCADE_PRECEDENTS).

Revision ID: 016
Revises: 015
Create Date: 2026-11-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "classification_precedents",
        sa.Column("id", sa.String(64), primary_key=True, comment="id записи реестра"),
        sa.Column("source", sa.String(32), nullable=False, server_default="eaeu_1999",
                  comment="Реестр: eaeu_1999 — сборник предварительных решений ЕАЭС"),
        sa.Column("code", sa.String(10), nullable=False, comment="Десятизначный код решения"),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("justification", sa.Text, comment="Обоснование органа: ОПИ, примечания, пояснения"),
        sa.Column("country", sa.String(8), comment="KZ, RU, BY, KG, AM"),
        sa.Column("version_id", sa.String(64), comment="Версия записи в реестре"),
        sa.Column("snapshot", sa.Date, nullable=False, comment="Дата снимка реестра"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("idx_precedents_code", "classification_precedents", ["code"])


def downgrade() -> None:
    op.drop_index("idx_precedents_code", table_name="classification_precedents")
    op.drop_table("classification_precedents")
