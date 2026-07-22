"""add the pass event timeline to contacts

Stores what happened during the pass (AOS, recording start, signal acquired and
lost, decode start and result) so it can be read back long after the agent's
log has scrolled away.

Guarded like the earlier migrations so re-running is a no-op.

Revision ID: 0005_add_pass_events
Revises: 0004_add_telemetry
Create Date: 2026-07-22
"""
import sqlalchemy as sa

from alembic import op

revision = "0005_add_pass_events"
down_revision = "0004_add_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = {c["name"] for c in insp.get_columns("contacts")}

    if "events" not in existing:
        with op.batch_alter_table("contacts") as batch:
            batch.add_column(sa.Column("events", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("contacts") as batch:
        batch.drop_column("events")
