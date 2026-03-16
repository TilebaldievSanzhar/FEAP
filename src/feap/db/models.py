from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
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
    valid_from: Mapped[dt.date | None] = mapped_column(Date)
    valid_to: Mapped[dt.date | None] = mapped_column(Date)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_vat_exemption_code_pattern", "code_pattern"),
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
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
