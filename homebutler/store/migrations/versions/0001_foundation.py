"""Foundation: households, members, keys, remotes, pairing codes, system state, config sources.

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

from homebutler.store.models import UTCDateTime

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = ("households", "members", "api_tokens", "remotes", "pairing_codes", "system_state", "config_sources")


def lock_down(tables):
    """PostgreSQL: row level security with no policy, so Supabase's public Data API sees nothing.
    The service connects as the tables' owner and is not limited by it."""
    if op.get_bind().dialect.name == "postgresql":
        for table in tables:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def upgrade():
    op.create_table(
        "households",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("key", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("ha_floor_id", sa.String(80), unique=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
    )
    op.create_table(
        "members",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("line_user_id", sa.String(64), unique=True),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("butler_style", sa.Text, nullable=False),
        sa.Column("receives_alerts", sa.Boolean, nullable=False),
        sa.Column("notion_database_id", sa.String(64), nullable=False),
        sa.Column("notion_filter", sa.Text, nullable=False),
        sa.Column("notion_writable", sa.Boolean, nullable=False),
        sa.Column("removed_at", UTCDateTime()),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
    )
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("member_id", sa.Integer, sa.ForeignKey("members.id")),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("scopes", sa.JSON, nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("last_used_at", UTCDateTime()),
        sa.Column("revoked_at", UTCDateTime()),
    )
    op.create_table(
        "remotes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("removed_at", UTCDateTime()),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
    )
    op.create_table(
        "pairing_codes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("member_id", sa.Integer, sa.ForeignKey("members.id")),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("expires_at", UTCDateTime(), nullable=False),
        sa.Column("approved_at", UTCDateTime()),
        sa.Column("used_at", UTCDateTime()),
    )
    op.create_table(
        "system_state",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
    )
    op.create_table(
        "config_sources",
        sa.Column("name", sa.String(80), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("synced_at", UTCDateTime(), nullable=False),
        sa.Column("rows", sa.Integer, nullable=False),
        sa.Column("problems", sa.JSON, nullable=False),
    )
    lock_down(TABLES)


def downgrade():
    for table in reversed(TABLES):
        op.drop_table(table)
