from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── ТН ВЭД tree ─────────────────────────────────────────────────────────────

class TnvedNode(Base):
    __tablename__ = "tnved_tree"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    code_formatted: Mapped[str | None] = mapped_column(String(14))
    level: Mapped[int] = mapped_column(Integer, nullable=False, comment="0=section,1=group,2=position,3=subposition,4=subsubposition")
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("tnved_tree.id"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_full: Mapped[str | None] = mapped_column(Text)

    section_roman: Mapped[str | None] = mapped_column(String(5), comment="Roman numeral of section")
    group_code: Mapped[str | None] = mapped_column(String(2))

    duty_rate: Mapped[str | None] = mapped_column(String(200))
    vat_rate: Mapped[str | None] = mapped_column(String(20))
    excise: Mapped[str | None] = mapped_column(String(200))
    unit_primary: Mapped[str | None] = mapped_column(String(50))
    unit_secondary: Mapped[str | None] = mapped_column(String(50))

    is_leaf: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_from: Mapped[dt.date | None] = mapped_column(Date)
    valid_to: Mapped[dt.date | None] = mapped_column(Date)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    parent: Mapped[TnvedNode | None] = relationship("TnvedNode", remote_side="TnvedNode.id", back_populates="children")
    children: Mapped[list[TnvedNode]] = relationship("TnvedNode", back_populates="parent")

    __table_args__ = (
        Index("idx_tnved_parent", "parent_id"),
        Index("idx_tnved_level", "level"),
        Index("idx_tnved_section", "section_roman"),
        Index("idx_tnved_group", "group_code"),
    )

    def __repr__(self) -> str:
        return f"<TnvedNode {self.code} L{self.level} '{self.name[:40]}'>"


# ── Освобождения от НДС ──────────────────────────────────────────────────────

class VatExemption(Base):
    __tablename__ = "vat_exemptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code_pattern: Mapped[str] = mapped_column(
        String(10), nullable=False,
        comment="Code or prefix, e.g. '3004' matches all 3004*",
    )
    exemption_type: Mapped[str] = mapped_column(
        String(10), nullable=False,
        comment="full | reduced",
    )
    rate_override: Mapped[str | None] = mapped_column(
        String(20), comment="e.g. '0%' for full exemption",
    )
    legal_ref: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    condition: Mapped[str | None] = mapped_column(
        Text, comment="Условие применения (целевое назначение); NULL = безусловно",
    )
    source: Mapped[str | None] = mapped_column(
        String(40), comment="nk289 | pkm816_a1 | pkm816_a2 | pkm249 | pkm264",
    )
    valid_from: Mapped[dt.date | None] = mapped_column(Date)
    valid_to: Mapped[dt.date | None] = mapped_column(Date)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_vat_exemption_code_pattern", "code_pattern"),
        Index("idx_vat_exemption_source", "source"),
    )

    def __repr__(self) -> str:
        return f"<VatExemption {self.code_pattern} {self.exemption_type}>"


# ── Ставки акцизов ──────────────────────────────────────────────────────────

class ExciseRate(Base):
    __tablename__ = "excise_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    code_pattern: Mapped[str] = mapped_column(
        String(14), nullable=False,
        comment="TNVED code or prefix, e.g. '2203' or '271012'",
    )
    product_name: Mapped[str] = mapped_column(Text, nullable=False)
    rate: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="Rate amount, e.g. '300', '10000'",
    )
    rate_unit: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="Unit: сом/литр, сом/тонну, сом/1000 шт, сом/кг, сом/шт",
    )
    legal_ref: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[dt.date | None] = mapped_column(Date)
    valid_to: Mapped[dt.date | None] = mapped_column(Date)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_excise_rate_code_pattern", "code_pattern"),
    )

    def __repr__(self) -> str:
        return f"<ExciseRate {self.code_pattern} {self.rate} {self.rate_unit}>"


# ── Меры защиты внутреннего рынка ЕАЭС (реестр ЕЭК) ─────────────────────────

class TradeRemedy(Base):
    """Антидемпинговая / специальная / компенсационная мера ЕАЭС."""

    __tablename__ = "trade_remedies"

    id: Mapped[int] = mapped_column(primary_key=True)
    measure_no: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="antidumping | special | countervailing",
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    product_description: Mapped[str | None] = mapped_column(Text)
    countries: Mapped[list[str]] = mapped_column(ARRAY(String(3)), nullable=False)
    codes: Mapped[list[str]] = mapped_column(ARRAY(String(10)), nullable=False)
    rates: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    legal_basis: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="applied | suspended | expired",
    )
    valid_from: Mapped[dt.date | None] = mapped_column(Date)
    valid_to: Mapped[dt.date | None] = mapped_column(Date)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_modified: Mapped[str | None] = mapped_column(String(40))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("idx_trade_remedies_status", "status"),)

    def __repr__(self) -> str:
        return f"<TradeRemedy {self.measure_no} {self.kind} {self.status}>"


class TradeAgreement(Base):
    """Торговый режим по стране происхождения (ЗСТ ЕАЭС, члены ЕАЭС)."""

    __tablename__ = "trade_agreements"

    id: Mapped[int] = mapped_column(primary_key=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    regime: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="eaeu_member | fta | fta_interim",
    )
    agreement: Mapped[str] = mapped_column(Text, nullable=False)
    legal_ref: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="in_force | signed | terminated",
    )
    in_force_from: Mapped[dt.date | None] = mapped_column(Date)
    in_force_to: Mapped[dt.date | None] = mapped_column(Date)
    origin_proof: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("country", "agreement", name="uq_trade_agreement_country"),
        Index("idx_trade_agreements_country", "country"),
    )

    def __repr__(self) -> str:
        return f"<TradeAgreement {self.country} {self.regime} {self.status}>"


# ── Примечания к разделам / группам ──────────────────────────────────────────

class TnvedNote(Base):
    __tablename__ = "tnved_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(10), nullable=False, comment="section|group")
    scope_code: Mapped[str] = mapped_column(String(10), nullable=False, comment="XVI or 85")
    note_number: Mapped[str | None] = mapped_column(String(10))
    note_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="inclusive|exclusive|definition|general")
    note_text: Mapped[str] = mapped_column(Text, nullable=False)
    references_to: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_notes_scope", "scope_type", "scope_code"),
        Index("idx_notes_type", "note_type"),
    )


# ── Пояснения (полные секции, прямая загрузка по коду) ────────────────────────

class ExplanatoryNote(Base):
    __tablename__ = "tnved_explanatory_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    group_code: Mapped[str] = mapped_column(String(2), nullable=False)
    code_level: Mapped[int] = mapped_column(Integer, nullable=False, comment="1=group,2=position,3=subposition")
    section_type: Mapped[str | None] = mapped_column(String(20), comment="general|includes|excludes")
    title: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_tokens: Mapped[int | None] = mapped_column(Integer, comment="Approximate token count")

    source_file: Mapped[str | None] = mapped_column(String(100))
    page_number: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_expl_group", "group_code"),
    )


# ── ОПИ (правила интерпретации) ─────────────────────────────────────────────

class OpiRule(Base):
    __tablename__ = "tnved_opi"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_number: Mapped[str] = mapped_column(String(5), nullable=False, unique=True)
    rule_text: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_instruction: Mapped[str] = mapped_column(Text, nullable=False)
    applicable_levels: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    priority: Mapped[int | None] = mapped_column(Integer)


# ── Нетарифные меры (НТР) ────────────────────────────────────────────────────

class NtmMeasureType(Base):
    __tablename__ = "ntm_measure_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, comment="1-13, TWS.BY R12 Kind")
    name_ru: Mapped[str] = mapped_column(Text, nullable=False)
    name_en: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)

    measures: Mapped[list[NtmMeasure]] = relationship("NtmMeasure", back_populates="measure_type")

    def __repr__(self) -> str:
        return f"<NtmMeasureType {self.kind} '{self.name_ru}'>"


class NtmMeasure(Base):
    __tablename__ = "ntm_measures"

    id: Mapped[int] = mapped_column(primary_key=True)
    measure_type_id: Mapped[int] = mapped_column(ForeignKey("ntm_measure_types.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    legal_basis: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    additional_info: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[dt.date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[dt.date | None] = mapped_column(Date)
    scope: Mapped[str] = mapped_column(String(10), nullable=False, default="eaeu")
    country_code: Mapped[str | None] = mapped_column(String(2))
    note: Mapped[str | None] = mapped_column(Text)
    data_source: Mapped[str | None] = mapped_column(String(30))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    measure_type: Mapped[NtmMeasureType] = relationship("NtmMeasureType", back_populates="measures")
    codes: Mapped[list[NtmMeasureCode]] = relationship("NtmMeasureCode", back_populates="measure")

    __table_args__ = (
        Index("idx_ntm_measures_type", "measure_type_id"),
        Index("idx_ntm_measures_valid", "valid_from", "valid_to"),
        Index("idx_ntm_measures_scope", "scope", "country_code"),
    )

    def __repr__(self) -> str:
        return f"<NtmMeasure {self.id} '{self.name[:50]}'>"


class NtmMeasureCode(Base):
    __tablename__ = "ntm_measure_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    measure_id: Mapped[int] = mapped_column(ForeignKey("ntm_measures.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(10), nullable=False)
    code_level: Mapped[int] = mapped_column(Integer, nullable=False)
    product_description: Mapped[str | None] = mapped_column(Text)
    applies_to_children: Mapped[bool] = mapped_column(Boolean, default=True)
    valid_from: Mapped[dt.date | None] = mapped_column(Date)
    valid_to: Mapped[dt.date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    measure: Mapped[NtmMeasure] = relationship("NtmMeasure", back_populates="codes")

    __table_args__ = (
        Index("idx_ntm_codes_code", "code"),
        Index("idx_ntm_codes_measure", "measure_id"),
    )

    def __repr__(self) -> str:
        return f"<NtmMeasureCode measure={self.measure_id} code={self.code}>"


# ── Сессии классификации ─────────────────────────────────────────────────────

class ClassificationSession(Base):
    __tablename__ = "classification_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    state: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Владелец сессии; NULL — сессии до миграции 012 и внутренние (воркер).
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("idx_sessions_org", "org_id"),)


# ── Async jobs (batch classification, OCR-derived) ────────────────────────────

class ClassificationJob(Base):
    __tablename__ = "classification_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Владелец задачи; NULL — задачи до миграции 012 (доступны как раньше).
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    total_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    needs_input_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancelled_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    parent_ocr_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    items: Mapped[list[ClassificationJobItem]] = relationship(
        "ClassificationJobItem", back_populates="job", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_jobs_status", "status"),
        Index("idx_jobs_expires", "expires_at"),
        Index("idx_jobs_parent_ocr", "parent_ocr_job_id"),
        Index("idx_jobs_org", "org_id"),
    )

    def __repr__(self) -> str:
        return f"<ClassificationJob {self.id} {self.job_type} {self.status}>"


class ClassificationJobItem(Base):
    __tablename__ = "classification_job_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("classification_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    product_description: Mapped[str] = mapped_column(Text, nullable=False)
    product_card: Mapped[dict | None] = mapped_column(JSONB)
    additional_info: Mapped[dict | None] = mapped_column(JSONB)
    source_ref: Mapped[dict | None] = mapped_column(JSONB)

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    classification_session_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("classification_sessions.session_id", ondelete="SET NULL"),
    )

    result: Mapped[dict | None] = mapped_column(JSONB)
    partial: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    job: Mapped[ClassificationJob] = relationship("ClassificationJob", back_populates="items")

    __table_args__ = (
        UniqueConstraint("job_id", "position", name="uq_job_item_position"),
        Index("idx_job_items_job", "job_id"),
        Index("idx_job_items_status", "status"),
        Index("idx_job_items_session", "classification_session_id"),
        Index("idx_job_items_running_updated", "status", "updated_at"),
    )

    def __repr__(self) -> str:
        return f"<ClassificationJobItem {self.id} job={self.job_id} pos={self.position} {self.status}>"


class ClassificationJobEvent(Base):
    __tablename__ = "classification_job_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("classification_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("classification_job_items.id", ondelete="CASCADE"),
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_events_job_created", "job_id", "created_at"),
    )


# ── OCR jobs (document → raw text → structured ProductCard[]) ────────────────

class OcrJob(Base):
    __tablename__ = "ocr_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment="pending | extracting | structuring | completed | failed | cancelled",
    )
    stage: Mapped[str | None] = mapped_column(String(32))

    # Input
    files: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    auto_classify: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Владелец задачи; NULL — задачи до миграции 012 (доступны как раньше).
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # Extract stage
    extraction: Mapped[list[dict] | None] = mapped_column(JSONB)

    # Structure stage
    products: Mapped[list[dict] | None] = mapped_column(JSONB)
    skipped_notes: Mapped[list[dict] | None] = mapped_column(JSONB)

    # Chain
    linked_classification_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_ocr_jobs_status", "status"),
        Index("idx_ocr_jobs_expires", "expires_at"),
        Index("idx_ocr_jobs_linked_classify", "linked_classification_job_id"),
        Index("idx_ocr_jobs_org", "org_id"),
    )

    def __repr__(self) -> str:
        return f"<OcrJob {self.id} {self.status} files={len(self.files or [])}>"


# ── ДТ-ядро: организации, каталог, досье, драфты (docs/dt_core_dev_plan.md) ──

class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="self",
        comment="self | client | counterparty",
    )
    inn: Mapped[str | None] = mapped_column(String(20))
    country: Mapped[str | None] = mapped_column(String(2))
    address: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )

    __table_args__ = (Index("idx_org_inn", "inn"),)

    def __repr__(self) -> str:
        return f"<Organization {self.id} '{self.name[:30]}' {self.kind}>"


class ProductCatalogItem(Base):
    __tablename__ = "product_catalog"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    sku: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    hs_code: Mapped[str | None] = mapped_column(String(10))
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    passport: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"),
        comment="Паспорт товара: параметры партии и обоснование кода (calc.schemas.PassportIn)",
    )
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approved_by: Mapped[str | None] = mapped_column(String(100))
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("org_id", "sku", name="uq_catalog_org_sku"),
        Index("idx_catalog_org", "org_id"),
        Index("idx_catalog_hs", "hs_code"),
    )


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"),
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft",
        comment="draft | classifying | review | ready | exported | archived",
    )
    procedure: Mapped[str] = mapped_column(String(10), nullable=False, default="IM40")
    data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict,
        comment="ShipmentIn без items (стороны, Incoterms, валюта, фрахт...)",
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )

    items: Mapped[list[ShipmentItemRow]] = relationship(
        "ShipmentItemRow", back_populates="shipment",
        cascade="all, delete-orphan", order_by="ShipmentItemRow.position",
    )
    drafts: Mapped[list[DtDraftRow]] = relationship(
        "DtDraftRow", back_populates="shipment", cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_shipments_org", "org_id"),
        Index("idx_shipments_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<Shipment {self.id} {self.status}>"


class ShipmentItemRow(Base):
    __tablename__ = "shipment_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("shipments.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, comment="ShipmentItemIn",
    )
    hs_code: Mapped[str | None] = mapped_column(
        String(10), comment="Денормализовано из data для выборок",
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )

    shipment: Mapped[Shipment] = relationship("Shipment", back_populates="items")

    __table_args__ = (
        UniqueConstraint("shipment_id", "position", name="uq_shipment_item_position"),
        Index("idx_shipment_items_shipment", "shipment_id"),
        Index("idx_shipment_items_hs", "hs_code"),
    )


class ShipmentDocumentRow(Base):
    __tablename__ = "shipment_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("shipments.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="other",
        comment="invoice | packing | cmr | awb | rail | contract | cert_origin | permit | other",
    )
    ocr_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    number: Mapped[str | None] = mapped_column(String(100))
    doc_date: Mapped[dt.date | None] = mapped_column(Date)
    extracted: Mapped[dict | None] = mapped_column(
        JSONB, comment="Структурированные поля из OCR/LLM-экстрактора (M2)",
    )
    file_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("idx_shipment_docs_shipment", "shipment_id"),)


class DtDraftRow(Base):
    __tablename__ = "dt_drafts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("shipments.id", ondelete="CASCADE"),
        nullable=False,
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="ready | has_errors | needs_input",
    )
    structure_version: Mapped[str] = mapped_column(String(30), nullable=False)
    graphs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    validation_report: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    missing_parameters: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    shipment: Mapped[Shipment] = relationship("Shipment", back_populates="drafts")

    __table_args__ = (
        UniqueConstraint("shipment_id", "version", name="uq_dt_draft_version"),
        Index("idx_dt_drafts_shipment", "shipment_id"),
    )

    def __repr__(self) -> str:
        return f"<DtDraft {self.id} v{self.version} {self.status}>"


class ExchangeRate(Base):
    """Официальный курс НБКР, действующий в календарный день rate_date.

    Строка на каждый день (включая выходные), см. refsync.nbkr: поиск курса —
    только точное совпадение дня; source='legacy' (до миграции 014) не читается.
    """

    __tablename__ = "exchange_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    rate_date: Mapped[dt.date] = mapped_column(
        Date, nullable=False, comment="День, в который курс действует (не дата публикации)",
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[object] = mapped_column(
        Numeric(18, 6), nullable=False, comment="Сом за единицу валюты (официальный курс НБКР)",
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="nbkr_daily",
        comment="nbkr_daily | nbkr_weekly | nbkr_archive | legacy",
    )
    effective_from: Mapped[dt.date | None] = mapped_column(
        Date, comment="Дата публикации НБКР, с которой курс действует",
    )
    nominal: Mapped[int | None] = mapped_column(Integer, comment="Номинал в публикации")
    fetched_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), comment="Когда курс получен из источника",
    )
    published_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), comment="Last-Modified фида НБКР",
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("rate_date", "currency", name="uq_exchange_rate_day_currency"),
        Index("idx_exchange_rates_lookup", "currency", "rate_date"),
    )

    def __repr__(self) -> str:
        return f"<ExchangeRate {self.currency} {self.rate_date} {self.rate}>"


# ── ЕТТ ЕАЭС из сводной редакции ЕЭК (tariff.ett, refsync.ett_sync) ────────

class EttRate(Base):
    """Лист ЕТТ: базовая ставка и номера примечаний (редакция PDF группы)."""

    __tablename__ = "ett_rates"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    group_code: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    base_rate: Mapped[str] = mapped_column(String(300), nullable=False)
    rate_raw: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[list[str]] = mapped_column(ARRAY(String(8)), nullable=False, default=list)
    unit: Mapped[str | None] = mapped_column(String(60))
    version: Mapped[dt.date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<EttRate {self.code} {self.base_rate} {self.notes}>"


class EttNoteRow(Base):
    """Примечание к ЕТТ: временная ставка и периоды действия."""

    __tablename__ = "ett_notes"

    note_id: Mapped[str] = mapped_column(String(8), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    periods: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[dt.date | None] = mapped_column(Date)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<EttNote {self.note_id} {self.kind}>"


# ── Обновление справочников (refsync, docs/refdata_sync.md) ─────────────────

class RefDataset(Base):
    """Состояние справочника: когда сверялись, чем закончилось, подпись эксперта."""

    __tablename__ = "ref_datasets"

    dataset: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown", comment="ok | failing | unknown",
    )
    last_attempt_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_change_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"),
        comment="Наблюдённые даты публикаций, предупреждения источника",
    )
    alerted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), comment="Последнее оповещение о сбое (троттлинг)",
    )
    attested_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), comment="Эксперт подтвердил актуальность (ручные справочники)",
    )
    attested_by: Mapped[str | None] = mapped_column(String(200))
    attested_note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<RefDataset {self.dataset} {self.status}>"


class RefSyncRun(Base):
    """Журнал запусков обновления справочника."""

    __tablename__ = "ref_sync_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dataset: Mapped[str] = mapped_column(String(40), nullable=False)
    trigger: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="cron | startup | on_demand | manual",
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="ok | no_change | failed | skipped",
    )
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"),
    )
    error: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    raw_sha256: Mapped[str | None] = mapped_column(String(64))
    raw_gzip: Mapped[bytes | None] = mapped_column(
        LargeBinary, comment="Сжатый ответ источника — только когда содержимое изменилось",
    )

    __table_args__ = (
        Index("idx_ref_sync_runs_dataset_started", "dataset", "started_at"),
    )

    def __repr__(self) -> str:
        return f"<RefSyncRun {self.dataset} {self.status} {self.started_at}>"


class RefChange(Base):
    """Изменение справочника: аудит, оповещение, подтверждение человеком."""

    __tablename__ = "ref_changes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dataset: Mapped[str] = mapped_column(String(40), nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False, comment="measure_no, код…")
    change: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="added | updated | missing | corrected",
    )
    fields: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False, default=list)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    effective_from: Mapped[dt.date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="applied — применено без подтверждения; pending — ждёт человека; "
                "approved / rejected — решение принято",
    )
    run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ref_sync_runs.id", ondelete="SET NULL"),
    )
    detected_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("idx_ref_changes_dataset_status", "dataset", "status"),
    )

    def __repr__(self) -> str:
        return f"<RefChange {self.dataset} {self.key} {self.change} {self.status}>"


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(
        String(30), nullable=False,
        comment="classification | ocr_document | dt_draft | export",
    )
    quantity: Mapped[object] = mapped_column(Numeric(12, 3), nullable=False, default=1)
    ref_type: Mapped[str | None] = mapped_column(String(30))
    ref_id: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_usage_org_kind_created", "org_id", "kind", "created_at"),
    )


# ── Auth (Track B1) ──────────────────────────────────────────────────────────

class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    email: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Хранится в нижнем регистре",
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False, comment="argon2id")
    name: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )

    memberships: Mapped[list[Membership]] = relationship(
        "Membership", back_populates="account", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("uq_accounts_email_lower", func.lower(email), unique=True),
    )

    def __repr__(self) -> str:
        return f"<Account {self.id} {self.email}>"


class RefClassifier(Base):
    """Классификаторы Решения КТС № 378 (+ национальные справочники КР).

    Одна строка = одно значение одного классификатора. verified=False —
    значение внесено из практики и требует сверки с актуальной редакцией
    Решения 378 (TODO-verify на кастдеве); ФЛК F-09 предупреждает об этом.
    """

    __tablename__ = "ref_classifiers"

    id: Mapped[int] = mapped_column(primary_key=True)
    classifier_code: Mapped[str] = mapped_column(
        String(40), nullable=False,
        comment="delivery_terms | transport_types | customs_procedures | "
                "mos_methods | deal_nature | doc_kinds | payment_kinds | ...",
    )
    item_code: Mapped[str] = mapped_column(String(20), nullable=False)
    name_ru: Mapped[str] = mapped_column(Text, nullable=False)
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="False = сверить с актуальной редакцией Решения 378",
    )
    valid_from: Mapped[dt.date | None] = mapped_column(Date)
    valid_to: Mapped[dt.date | None] = mapped_column(Date)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "classifier_code", "item_code", name="uq_ref_classifier_item"
        ),
        Index("idx_ref_classifiers_code", "classifier_code"),
    )

    def __repr__(self) -> str:
        return f"<RefClassifier {self.classifier_code}:{self.item_code}>"


class Membership(Base):
    __tablename__ = "memberships"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="declarant",
        comment="owner | declarant | viewer",
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    account: Mapped[Account] = relationship("Account", back_populates="memberships")
    organization: Mapped[Organization] = relationship("Organization")

    __table_args__ = (
        UniqueConstraint("account_id", "org_id", name="uq_membership_account_org"),
        Index("idx_memberships_org", "org_id"),
        Index("idx_memberships_account", "account_id"),
    )

    def __repr__(self) -> str:
        return f"<Membership {self.account_id}@{self.org_id} {self.role}>"
