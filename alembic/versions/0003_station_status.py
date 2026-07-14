"""add station_status table for the mobile ground-station position

The agent reports its auto-detected location to POST /observer; the app
persists it in this single-row table so the map pin and pass predictions
follow the station. Guarded so it is a no-op when the table already exists
(e.g. created by init_db's create_all on a fresh local DB).

Revision ID: 0003_station_status
Revises: 0002_add_ops_columns
Create Date: 2026-07-14
"""
import sqlalchemy as sa

from alembic import op

revision = "0003_station_status"
down_revision = "0002_add_ops_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "station_status" in sa.inspect(bind).get_table_names():
        return

    op.create_table(
        "station_status",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("station_status")
