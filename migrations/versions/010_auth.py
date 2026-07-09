"""Auth (Track B1): аккаунты и членства в организациях.

- accounts: email (уникален по lower(email)) + argon2id-хэш пароля;
- memberships: аккаунт ↔ организация с ролью owner | declarant | viewer.

Организации уже существуют (миграция 009); org_id на рабочих таблицах
не меняется — auth лишь начинает наполнять его из токена вместо X-Org-Id.

Revision ID: 010
Revises: 009
Create Date: 2026-07-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.Text, nullable=False,
                  comment="Хранится в нижнем регистре"),
        sa.Column("password_hash", sa.Text, nullable=False, comment="argon2id"),
        sa.Column("name", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index(
        "uq_accounts_email_lower", "accounts",
        [sa.text("lower(email)")], unique=True,
    )

    op.create_table(
        "memberships",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("account_id", UUID(as_uuid=True),
                  sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="declarant",
                  comment="owner | declarant | viewer"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("account_id", "org_id", name="uq_membership_account_org"),
    )
    op.create_index("idx_memberships_org", "memberships", ["org_id"])
    op.create_index("idx_memberships_account", "memberships", ["account_id"])


def downgrade() -> None:
    op.drop_table("memberships")
    op.drop_index("uq_accounts_email_lower", table_name="accounts")
    op.drop_table("accounts")
