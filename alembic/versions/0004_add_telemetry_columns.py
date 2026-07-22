"""add contact_type and telemetry to contacts

A pass no longer always produces a picture: Orbcomm gives decoded frames
instead. contact_type says which kind it was, telemetry holds the decoder's
output (frame counts, PER, packet types, ephemeris) as JSON.

Existing rows are all image passes, so contact_type backfills to 'image'.
Guarded like 0002 so re-running is a no-op.

Revision ID: 0004_add_telemetry
Revises: 0003_station_status
Create Date: 2026-07-22
"""
import sqlalchemy as sa

from alembic import op

revision = "0004_add_telemetry"
down_revision = "0003_station_status"
branch_labels = None
depends_on = None

_NEW_COLUMNS = {
    # nullable + server_default so the migration works on a table with rows;
    # the model itself declares it NOT NULL with an application default.
    "contact_type": sa.Column(
        "contact_type", sa.String(), nullable=True, server_default="image"
    ),
    "telemetry": sa.Column("telemetry", sa.JSON(), nullable=True),
}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = {c["name"] for c in insp.get_columns("contacts")}

    to_add = {name: col for name, col in _NEW_COLUMNS.items() if name not in existing}
    if to_add:
        with op.batch_alter_table("contacts") as batch:
            for col in to_add.values():
                batch.add_column(col)

    # Everything recorded before this migration was an image pass.
    op.execute("UPDATE contacts SET contact_type = 'image' WHERE contact_type IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("contacts") as batch:
        for name in _NEW_COLUMNS:
            batch.drop_column(name)
