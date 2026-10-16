"""ЕТТ ЕАЭС из сводной редакции ЕЭК: ставки листьев и примечания со сроками.

1. ett_rates — лист ЕТТ: базовая ставка (формат tnved_tree.duty_rate), номера
   примечаний к ЕТТ, дата редакции PDF группы, ссылка на источник.
2. ett_notes — «Примечания к ЕТТ»: временные ставки и периоды действия
   (refsync.ett_sync, tariff.ett).

Ставка на дату = ставка примечания, если его период покрывает дату, иначе
базовая (tariff.ett.effective_rate). tnved_tree.duty_rate (снимок TWS) остаётся
запасным значением для дат раньше редакции группы.

Revision ID: 015
Revises: 014
Create Date: 2026-10-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ett_rates",
        sa.Column("code", sa.String(10), primary_key=True),
        sa.Column("group_code", sa.String(2), nullable=False),
        sa.Column("base_rate", sa.String(300), nullable=False,
                  comment="Базовая ставка в формате tnved_tree.duty_rate"),
        sa.Column("rate_raw", sa.Text, nullable=False, comment="Ставка как в PDF ЕЭК"),
        sa.Column("notes", postgresql.ARRAY(sa.String(8)), nullable=False,
                  server_default=sa.text("'{}'::varchar[]"),
                  comment="Номера примечаний к ЕТТ: «123С»"),
        sa.Column("unit", sa.String(60)),
        sa.Column("version", sa.Date, comment="Дата редакции PDF группы"),
        sa.Column("source_url", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("idx_ett_rates_group", "ett_rates", ["group_code"])

    op.create_table(
        "ett_notes",
        sa.Column("note_id", sa.String(8), primary_key=True),
        sa.Column("kind", sa.String(10), nullable=False, comment="rate | void | other"),
        sa.Column("periods", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"),
                  comment="[{rate, valid_from, valid_to, from_decision, scope}]"),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("version", sa.Date, comment="Дата редакции «Примечаний к ЕТТ»"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("ett_notes")
    op.drop_index("idx_ett_rates_group", table_name="ett_rates")
    op.drop_table("ett_rates")
