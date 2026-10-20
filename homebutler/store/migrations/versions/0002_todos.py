"""To-dos: light areas, templates (recurring), to-dos, their light areas, reminders already sent.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

from homebutler.store.models import UTCDateTime

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = ("light_areas", "todo_templates", "template_light_areas", "todos", "todo_light_areas", "reminder_log")


def upgrade():
    op.create_table(
        "light_areas",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id"), nullable=False),
        sa.Column("ha_target", sa.String(80), nullable=False, unique=True),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("source_name", sa.String(80), nullable=False),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("is_default", sa.Boolean, nullable=False),
        sa.Column("removed_at", UTCDateTime()),
    )
    op.create_table(
        "todo_templates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("rule", sa.String(16), nullable=False),
        sa.Column("weekdays", sa.JSON, nullable=False),
        sa.Column("month_day", sa.Integer),
        sa.Column("interval_days", sa.Integer),
        sa.Column("due_time", sa.Time),
        sa.Column("assignee_id", sa.Integer, sa.ForeignKey("members.id")),
        sa.Column("visibility", sa.String(8), nullable=False),
        sa.Column("light_reminder", sa.Boolean, nullable=False),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("last_generated_on", sa.Date),
        sa.Column("created_by_id", sa.Integer, sa.ForeignKey("members.id")),
        sa.Column("created_at", UTCDateTime(), nullable=False),
    )
    op.create_table(
        "template_light_areas",
        sa.Column("template_id", sa.Integer, sa.ForeignKey("todo_templates.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("light_area_id", sa.Integer, sa.ForeignKey("light_areas.id"), primary_key=True),
    )
    op.create_table(
        "todos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("household_id", sa.Integer, sa.ForeignKey("households.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("due_date", sa.Date),
        sa.Column("due_time", sa.Time),
        sa.Column("assignee_id", sa.Integer, sa.ForeignKey("members.id")),
        sa.Column("visibility", sa.String(8), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("read_only", sa.Boolean, nullable=False),
        sa.Column("external_id", sa.String(64)),
        sa.Column("light_reminder", sa.Boolean, nullable=False),
        sa.Column("template_id", sa.Integer, sa.ForeignKey("todo_templates.id")),
        sa.Column("created_by_id", sa.Integer, sa.ForeignKey("members.id")),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("completed_at", UTCDateTime()),
        sa.Column("archived_at", UTCDateTime()),
        sa.UniqueConstraint("assignee_id", "external_id", name="uq_todos_external"),
    )
    op.create_index("ix_todos_open", "todos", ["household_id", "status", "archived_at"])
    op.create_table(
        "todo_light_areas",
        sa.Column("todo_id", sa.Integer, sa.ForeignKey("todos.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("light_area_id", sa.Integer, sa.ForeignKey("light_areas.id"), primary_key=True),
    )
    op.create_table(
        "reminder_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("todo_id", sa.Integer, sa.ForeignKey("todos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.Integer, sa.ForeignKey("members.id"), nullable=False),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("sent_at", UTCDateTime(), nullable=False),
        sa.UniqueConstraint("todo_id", "member_id", "stage", name="uq_reminder_once"),
    )
    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade():
    op.drop_index("ix_todos_open", "todos")
    for table in reversed(TABLES):
        op.drop_table(table)
