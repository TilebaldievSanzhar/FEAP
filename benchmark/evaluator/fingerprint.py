"""Reference-data fingerprints for benchmark manifests.

A result is reproducible only on the same reference data: the nomenclature and
notes the cascade reads, and the rates, trade remedies, NTM measures and
exchange rates the payment engine reads. Hashes cover row content; surrogate
ids and sync timestamps are left out, so a re-seed with identical content keeps
the hash.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Iterable
from contextlib import closing

import psycopg2
from psycopg2 import sql
from sqlalchemy.engine import make_url

from feap.config import settings

#: Hashed in the v30–v32 manifests with exactly this query; unchanged so new
#: manifests stay comparable with the old ones.
CLASSIFICATION_TABLES: tuple[str, ...] = (
    'tnved_tree', 'tnved_notes', 'tnved_explanatory_notes', 'tnved_opi',
)

#: Payment, NTM and classifier data → columns left out of the content hash.
REFERENCE_TABLES: dict[str, tuple[str, ...]] = {
    'ett_rates': ('updated_at',),
    'ett_notes': ('updated_at',),
    'excise_rates': ('id', 'updated_at'),
    'vat_exemptions': ('id', 'updated_at'),
    'trade_remedies': ('id', 'updated_at'),
    'trade_agreements': ('id', 'updated_at'),
    'ref_classifiers': ('id', 'updated_at'),
    # NTM ids are foreign keys of ntm_measure_codes, so they stay in the hash.
    'ntm_measure_types': (),
    'ntm_measures': ('updated_at',),
    'ntm_measure_codes': ('updated_at',),
}

#: exchange_rates grows every day; only the dates a run uses are hashed.
_FX_VOLATILE: tuple[str, ...] = ('id', 'created_at', 'fetched_at')


def libpq_dsn(url: str | None = None) -> str:
    """SQLAlchemy URL ('postgresql+asyncpg://…') → DSN that psycopg2 accepts.

    settings.database_url_sync carries a '+psycopg2' driver suffix for
    SQLAlchemy, and libpq rejects any '+driver' scheme.
    """
    parsed = make_url(url or settings.DATABASE_URL)
    return parsed.set(drivername='postgresql').render_as_string(hide_password=False)


def _digest(cursor) -> dict:
    digest = hashlib.sha256()
    count = 0
    for (row,) in cursor:
        digest.update(json.dumps(row, sort_keys=True, ensure_ascii=False).encode())
        digest.update(b'\n')
        count += 1
    return {'rows': count, 'sha256': digest.hexdigest()}


def _exists(connection, table: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute('SELECT to_regclass(%s)', (table,))
        return cursor.fetchone()[0] is not None


def _hash_query(connection, table: str, query: sql.Composable, params=None) -> dict:
    """Hash one query; a table missing on an older schema is recorded, not fatal."""
    if not _exists(connection, table):
        return {'missing': True}
    with connection.cursor(name=f'fingerprint_{table}') as cursor:
        cursor.execute(query, params)
        return _digest(cursor)


def classification_fingerprint(connection) -> dict:
    return {
        table: _hash_query(connection, table, sql.SQL(
            "SELECT to_jsonb(t) - 'embedding' - 'updated_at' FROM {} AS t ORDER BY id"
        ).format(sql.Identifier(table)))
        for table in CLASSIFICATION_TABLES
    }


def payment_fingerprint(connection, fx_dates: Iterable[dt.date] = ()) -> dict:
    # Ordered by content: these tables have no stable id order across re-seeds.
    out = {
        table: _hash_query(connection, table, sql.SQL(
            'SELECT j FROM (SELECT to_jsonb(t) - {}::text[] AS j FROM {} AS t) AS s '
            'ORDER BY j::text'
        ).format(sql.Literal(list(volatile)), sql.Identifier(table)))
        for table, volatile in REFERENCE_TABLES.items()
    }
    days = sorted(set(fx_dates))
    if days:
        out['exchange_rates'] = {
            'dates': [d.isoformat() for d in days],
            **_hash_query(connection, 'exchange_rates', sql.SQL(
                'SELECT j FROM (SELECT to_jsonb(t) - {}::text[] AS j FROM exchange_rates AS t '
                'WHERE rate_date = ANY(%s)) AS s ORDER BY j::text'
            ).format(sql.Literal(list(_FX_VOLATILE))), (days,)),
        }
    return out


def refdata_status(connection) -> list[dict]:
    """Sync state of the reference datasets — recorded, never compared on resume."""
    if not _exists(connection, 'ref_datasets'):
        return []
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT dataset, status, last_success_at, last_change_at, attested_at, attested_by '
            'FROM ref_datasets ORDER BY dataset'
        )
        rows = cursor.fetchall()
    keys = ('dataset', 'status', 'last_success_at', 'last_change_at', 'attested_at', 'attested_by')
    return [
        {k: v.isoformat() if isinstance(v, dt.datetime) else v for k, v in zip(keys, row)}
        for row in rows
    ]


def database_fingerprint(*, payments: bool = False, fx_dates: Iterable[dt.date] = (),
                         dsn: str | None = None) -> dict:
    """Classification tables always; payment tables and FX days on request."""
    with closing(psycopg2.connect(dsn or libpq_dsn())) as connection, connection:
        fingerprint = classification_fingerprint(connection)
        if payments:
            fingerprint.update(payment_fingerprint(connection, fx_dates))
    return fingerprint


def database_status(dsn: str | None = None) -> list[dict]:
    with closing(psycopg2.connect(dsn or libpq_dsn())) as connection, connection:
        return refdata_status(connection)
