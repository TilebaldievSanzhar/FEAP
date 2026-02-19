from __future__ import annotations

import datetime as dt

from pgvector.sqlalchemy import Vector

from feap.config import settings
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


# ── Пояснения (чанки для RAG) ───────────────────────────────────────────────

class ExplanatoryNote(Base):
    __tablename__ = "tnved_explanatory_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    code_level: Mapped[int] = mapped_column(Integer, nullable=False)
    section_type: Mapped[str | None] = mapped_column(String(20), comment="general|includes|excludes|examples")
    content: Mapped[str] = mapped_column(Text, nullable=False)

    chunk_index: Mapped[int | None] = mapped_column(Integer)
    chunk_text: Mapped[str | None] = mapped_column(Text)
    embedding = mapped_column(Vector(settings.VOYAGE_EMBEDDING_DIM), nullable=True)

    source_file: Mapped[str | None] = mapped_column(String(100))
    page_number: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
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
