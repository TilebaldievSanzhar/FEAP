"""Обновление справочников: курс на каждый день, статусы, журнал, изменения.

1. exchange_rates — строка на каждый календарный день, в который курс действует
   (refsync.nbkr): effective_from (дата публикации НБКР), nominal, fetched_at,
   published_at. Существующие строки (по одной на дату публикации, без
   выходных) → source='legacy': курс на дату мог быть пропущен, чтение их
   не использует; сверка с архивом НБКР дозаполняет дни.
2. ref_datasets — состояние справочника (последняя попытка/успех/изменение,
   неудачи подряд, ошибка, подпись эксперта).
3. ref_sync_runs — журнал запусков (статистика, ошибка, сжатый ответ источника).
4. ref_changes — изменения справочников: аудит и подтверждение человеком.

Revision ID: 014
Revises: 013
Create Date: 2026-10-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("exchange_rates", sa.Column(
        "effective_from", sa.Date, comment="Дата публикации НБКР, с которой курс действует"))
    op.add_column("exchange_rates", sa.Column(
        "nominal", sa.Integer, comment="Номинал в публикации"))
    op.add_column("exchange_rates", sa.Column(
        "fetched_at", sa.DateTime(timezone=True), comment="Когда курс получен из источника"))
    op.add_column("exchange_rates", sa.Column(
        "published_at", sa.DateTime(timezone=True), comment="Last-Modified фида НБКР"))
    op.execute("UPDATE exchange_rates SET source = 'legacy' WHERE source = 'NBKR'")
    op.alter_column("exchange_rates", "source", server_default=None)

    op.create_table(
        "ref_datasets",
        sa.Column("dataset", sa.String(40), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="unknown",
                  comment="ok | failing | unknown"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_change_at", sa.DateTime(timezone=True)),
        sa.Column("consecutive_failures", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"),
                  comment="Наблюдённые даты публикаций, предупреждения источника"),
        sa.Column("alerted_at", sa.DateTime(timezone=True),
                  comment="Последнее оповещение о сбое (троттлинг)"),
        sa.Column("attested_at", sa.DateTime(timezone=True),
                  comment="Эксперт подтвердил актуальность (ручные справочники)"),
        sa.Column("attested_by", sa.String(200)),
        sa.Column("attested_note", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "ref_sync_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("dataset", sa.String(40), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False,
                  comment="cron | startup | on_demand | manual"),
        sa.Column("status", sa.String(20), nullable=False,
                  comment="ok | no_change | failed | skipped"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("stats", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.Text),
        sa.Column("source_url", sa.Text),
        sa.Column("raw_sha256", sa.String(64)),
        sa.Column("raw_gzip", sa.LargeBinary,
                  comment="Сжатый ответ источника — только когда содержимое изменилось"),
    )
    op.create_index("idx_ref_sync_runs_dataset_started", "ref_sync_runs", ["dataset", "started_at"])

    op.create_table(
        "ref_changes",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("dataset", sa.String(40), nullable=False),
        sa.Column("key", sa.String(100), nullable=False, comment="measure_no, код…"),
        sa.Column("change", sa.String(20), nullable=False,
                  comment="added | updated | missing | corrected"),
        sa.Column("fields", postgresql.ARRAY(sa.String(40)), nullable=False,
                  server_default=sa.text("'{}'::varchar[]")),
        sa.Column("before", postgresql.JSONB),
        sa.Column("after", postgresql.JSONB),
        sa.Column("effective_from", sa.Date),
        sa.Column("status", sa.String(20), nullable=False,
                  comment="applied | pending | approved | rejected"),
        sa.Column("run_id", sa.BigInteger,
                  sa.ForeignKey("ref_sync_runs.id", ondelete="SET NULL")),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", sa.String(200)),
        sa.Column("note", sa.Text),
    )
    op.create_index("idx_ref_changes_dataset_status", "ref_changes", ["dataset", "status"])


def downgrade() -> None:
    op.drop_index("idx_ref_changes_dataset_status", table_name="ref_changes")
    op.drop_table("ref_changes")
    op.drop_index("idx_ref_sync_runs_dataset_started", table_name="ref_sync_runs")
    op.drop_table("ref_sync_runs")
    op.drop_table("ref_datasets")
    op.execute("DELETE FROM exchange_rates WHERE source <> 'legacy'")
    op.execute("UPDATE exchange_rates SET source = 'NBKR'")
    op.alter_column("exchange_rates", "source", server_default="NBKR")
    op.drop_column("exchange_rates", "published_at")
    op.drop_column("exchange_rates", "fetched_at")
    op.drop_column("exchange_rates", "nominal")
    op.drop_column("exchange_rates", "effective_from")
