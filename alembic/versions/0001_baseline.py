"""baseline: contacts table as it existed before the operations columns

Represents the schema already deployed on Render. On a database that
already has the table (production) the create is skipped, so
`alembic upgrade head` is safe to run against the live DB without a
manual stamp; on a fresh database it creates the table.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-02
"""
import sqlalchemy as sa

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "contacts" in sa.inspect(bind).get_table_names():
        return  # already present on the deployed DB

    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("satellite", sa.String(), nullable=False),
        sa.Column("aos", sa.DateTime(timezone=True), nullable=False),
        sa.Column("los", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_s", sa.Integer(), nullable=False),
        sa.Column("max_elevation", sa.Float(), nullable=False),
        sa.Column("snr", sa.Float(), nullable=True),
        sa.Column("image_filename", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("contacts")
