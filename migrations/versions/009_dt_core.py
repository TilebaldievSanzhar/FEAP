"""ДТ-ядро: организации, каталог товаров, досье поставок, драфты ДТ, курсы, метеринг.

См. docs/dt_core_dev_plan.md. Ключевые решения:
- org_id заложен на всех таблицах с первого дня (auth появится в Track B,
  схема не изменится);
- findings живут внутри dt_drafts.validation_report (JSONB), отдельной
  таблицы нет — они умирают вместе с версией драфта;
- usage_events — метеринг для будущего биллинга, пишется уже сейчас.

Revision ID: 009
Revises: 007
Create Date: 2026-07-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "009"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ── organizations ────────────────────────────────────────────────────────
    op.create_table(
        "organizations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="self",
                  comment="self | client | counterparty"),
        sa.Column("inn", sa.String(20), nullable=True),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("address", sa.Text, nullable=True),
        sa.Column("extra", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("idx_org_inn", "organizations", ["inn"])

    # ── product_catalog ──────────────────────────────────────────────────────
    op.create_table(
        "product_catalog",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sku", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("hs_code", sa.String(10), nullable=True),
        sa.Column("attributes", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("usage_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("approved_by", sa.String(100), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "sku", name="uq_catalog_org_sku"),
    )
    op.create_index("idx_catalog_org", "product_catalog", ["org_id"])
    op.create_index("idx_catalog_hs", "product_catalog", ["hs_code"])

    # ── shipments ────────────────────────────────────────────────────────────
    op.create_table(
        "shipments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft",
                  comment="draft | classifying | review | ready | exported | archived"),
        sa.Column("procedure", sa.String(10), nullable=False, server_default="IM40"),
        sa.Column("data", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"),
                  comment="ShipmentIn без items"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("idx_shipments_org", "shipments", ["org_id"])
    op.create_index("idx_shipments_status", "shipments", ["status"])

    # ── shipment_items ───────────────────────────────────────────────────────
    op.create_table(
        "shipment_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("shipment_id", UUID(as_uuid=True),
                  sa.ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("data", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"),
                  comment="ShipmentItemIn"),
        sa.Column("hs_code", sa.String(10), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("shipment_id", "position", name="uq_shipment_item_position"),
    )
    op.create_index("idx_shipment_items_shipment", "shipment_items", ["shipment_id"])
    op.create_index("idx_shipment_items_hs", "shipment_items", ["hs_code"])

    # ── shipment_documents ───────────────────────────────────────────────────
    op.create_table(
        "shipment_documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("shipment_id", UUID(as_uuid=True),
                  sa.ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="other",
                  comment="invoice | packing | cmr | awb | rail | contract | cert_origin | permit | other"),
        sa.Column("ocr_job_id", UUID(as_uuid=True), nullable=True),
        sa.Column("number", sa.String(100), nullable=True),
        sa.Column("doc_date", sa.Date, nullable=True),
        sa.Column("extracted", JSONB, nullable=True,
                  comment="Структурированные поля из OCR/LLM-экстрактора (M2)"),
        sa.Column("file_ref", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("idx_shipment_docs_shipment", "shipment_documents", ["shipment_id"])

    # ── dt_drafts ────────────────────────────────────────────────────────────
    op.create_table(
        "dt_drafts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("shipment_id", UUID(as_uuid=True),
                  sa.ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False,
                  comment="ready | has_errors | needs_input"),
        sa.Column("structure_version", sa.String(30), nullable=False),
        sa.Column("graphs", JSONB, nullable=False),
        sa.Column("validation_report", JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("missing_parameters", JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("shipment_id", "version", name="uq_dt_draft_version"),
    )
    op.create_index("idx_dt_drafts_shipment", "dt_drafts", ["shipment_id"])

    # ── exchange_rates ───────────────────────────────────────────────────────
    op.create_table(
        "exchange_rates",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("rate_date", sa.Date, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(18, 6), nullable=False,
                  comment="Сом за единицу валюты (официальный курс НБКР)"),
        sa.Column("source", sa.String(20), nullable=False, server_default="NBKR"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("rate_date", "currency", name="uq_exchange_rate_day_currency"),
    )
    op.create_index("idx_exchange_rates_lookup", "exchange_rates", ["currency", "rate_date"])

    # ── usage_events ─────────────────────────────────────────────────────────
    op.create_table(
        "usage_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("org_id", UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(30), nullable=False,
                  comment="classification | ocr_document | dt_draft | export"),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False, server_default="1"),
        sa.Column("ref_type", sa.String(30), nullable=True),
        sa.Column("ref_id", sa.String(64), nullable=True),
        sa.Column("meta", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("idx_usage_org_kind_created", "usage_events",
                    ["org_id", "kind", "created_at"])


def downgrade() -> None:
    op.drop_table("usage_events")
    op.drop_table("exchange_rates")
    op.drop_table("dt_drafts")
    op.drop_table("shipment_documents")
    op.drop_table("shipment_items")
    op.drop_table("shipments")
    op.drop_table("product_catalog")
    op.drop_table("organizations")
