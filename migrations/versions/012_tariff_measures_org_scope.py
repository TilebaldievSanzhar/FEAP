"""Тарифные меры (антидемпинг/ЗСТ/условный НДС) и org-скоуп задач.

1. trade_remedies — меры защиты внутреннего рынка ЕАЭС (антидемпинговые,
   специальные, компенсационные). Источник — реестр ЕЭК
   remedies.eaeunion.org (scripts/fetch/eaeu_remedies.py →
   data/trade_remedies_seed.json → scripts/seed/trade_remedies.py).
2. trade_agreements — торговые режимы по стране происхождения: ЗСТ ЕАЭС,
   временные соглашения, члены ЕАЭС (data/trade_agreements_seed.json).
3. vat_exemptions.condition/source — условные льготы НДС (ставка 0% только
   при целевом назначении, ПКМ КР № 816/249/264) и происхождение строки.
4. org_id в classification_jobs / ocr_jobs / classification_sessions —
   изоляция данных между организациями (раньше эндпоинты /jobs/*, /ocr/jobs/*
   и /classify/{session}/answer отдавали чужие данные по UUID).

Revision ID: 012
Revises: 011
Create Date: 2026-10-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trade_remedies",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("measure_no", sa.String(20), nullable=False,
                  comment="Номер в реестре ЕЭК, например 'AD-32'"),
        sa.Column("kind", sa.String(20), nullable=False,
                  comment="antidumping | special | countervailing"),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("product_description", sa.Text),
        sa.Column("countries", postgresql.ARRAY(sa.String(3)), nullable=False,
                  comment="ISO alpha-2 стран происхождения; 'ALL' = любая страна"),
        sa.Column("codes", postgresql.ARRAY(sa.String(10)), nullable=False,
                  comment="Коды/префиксы ТН ВЭД (только цифры)"),
        sa.Column("rates", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb"),
                  comment="[{rate_pct, country, producers, variant}]"),
        sa.Column("notes", sa.Text, comment="Квоты, исключения, ценовые обязательства"),
        sa.Column("legal_basis", sa.Text),
        sa.Column("status", sa.String(20), nullable=False,
                  comment="applied | suspended | expired"),
        sa.Column("valid_from", sa.Date),
        sa.Column("valid_to", sa.Date),
        sa.Column("needs_review", sa.Boolean, nullable=False,
                  server_default=sa.text("false"),
                  comment="true = ставки не разобраны автоматически, нужен ручной ввод"),
        sa.Column("source_url", sa.Text),
        sa.Column("source_modified", sa.String(40)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("measure_no", name="uq_trade_remedy_measure_no"),
    )
    op.create_index("idx_trade_remedies_status", "trade_remedies", ["status"])

    op.create_table(
        "trade_agreements",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("regime", sa.String(20), nullable=False,
                  comment="eaeu_member | fta | fta_interim"),
        sa.Column("agreement", sa.Text, nullable=False),
        sa.Column("legal_ref", sa.Text),
        sa.Column("status", sa.String(20), nullable=False,
                  comment="in_force | signed | terminated"),
        sa.Column("in_force_from", sa.Date),
        sa.Column("in_force_to", sa.Date),
        sa.Column("origin_proof", sa.Text,
                  comment="Документ о происхождении для преференции"),
        sa.Column("note", sa.Text),
        sa.Column("source_url", sa.Text),
        sa.Column("verified", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("country", "agreement", name="uq_trade_agreement_country"),
    )
    op.create_index("idx_trade_agreements_country", "trade_agreements", ["country"])

    op.add_column("vat_exemptions", sa.Column(
        "condition", sa.Text, nullable=True,
        comment="Условие применения (целевое назначение); NULL = безусловно",
    ))
    op.add_column("vat_exemptions", sa.Column(
        "source", sa.String(40), nullable=True,
        comment="Набор данных: nk289 | pkm816_a1 | pkm816_a2 | pkm249 | pkm264",
    ))
    op.create_index("idx_vat_exemption_source", "vat_exemptions", ["source"])
    # Существующие строки — освобождения ст. 289 НК КР (scripts/seed/vat_exemptions.py).
    op.execute(
        "UPDATE vat_exemptions SET source = 'nk289' "
        "WHERE source IS NULL AND legal_ref LIKE 'НК КР, ст. 289%'"
    )

    for table, idx in (
        ("classification_jobs", "idx_jobs_org"),
        ("ocr_jobs", "idx_ocr_jobs_org"),
        ("classification_sessions", "idx_sessions_org"),
    ):
        op.add_column(table, sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_index(idx, table, ["org_id"])

    # Задачи, созданные после Track B1, хранили организацию в params.
    op.execute(
        """
        UPDATE classification_jobs
        SET org_id = (params->>'org_id')::uuid
        WHERE org_id IS NULL
          AND params ? 'org_id'
          AND params->>'org_id' ~ '^[0-9a-fA-F-]{36}$'
        """
    )


def downgrade() -> None:
    for table, idx in (
        ("classification_sessions", "idx_sessions_org"),
        ("ocr_jobs", "idx_ocr_jobs_org"),
        ("classification_jobs", "idx_jobs_org"),
    ):
        op.drop_index(idx, table_name=table)
        op.drop_column(table, "org_id")

    op.drop_index("idx_vat_exemption_source", table_name="vat_exemptions")
    op.drop_column("vat_exemptions", "source")
    op.drop_column("vat_exemptions", "condition")

    op.drop_index("idx_trade_agreements_country", table_name="trade_agreements")
    op.drop_table("trade_agreements")
    op.drop_index("idx_trade_remedies_status", table_name="trade_remedies")
    op.drop_table("trade_remedies")
