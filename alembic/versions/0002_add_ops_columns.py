"""add operations columns and the unique-pass constraint

Adds avg_snr / quality / notes and the (satellite, aos) unique constraint
introduced in iterations F (A4) and 5. Each step is guarded so the
migration is a no-op if a column/constraint already exists, and duplicate
passes are removed before the constraint is enforced.

Revision ID: 0002_add_ops_columns
Revises: 0001_baseline
Create Date: 2026-07-02
"""
import sqlalchemy as sa

from alembic import op

revision = "0002_add_ops_columns"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

_NEW_COLUMNS = {
    "avg_snr": sa.Column("avg_snr", sa.Float(), nullable=True),
    "quality": sa.Column("quality", sa.String(), nullable=True),
    "notes": sa.Column("notes", sa.String(), nullable=True),
}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_cols = {c["name"] for c in insp.get_columns("contacts")}
    existing_uniques = {c["name"] for c in insp.get_unique_constraints("contacts")}

    to_add = {name: col for name, col in _NEW_COLUMNS.items() if name not in existing_cols}
    if to_add:
        with op.batch_alter_table("contacts") as batch:
            for col in to_add.values():
                batch.add_column(col)

    # Enforce one contact per (satellite, aos): drop duplicates first, keeping
    # the earliest row, then add the constraint. Retries could have created
    # duplicates before the app-level idempotency guard existed.
    op.execute(
        "DELETE FROM contacts WHERE id NOT IN "
        "(SELECT MIN(id) FROM contacts GROUP BY satellite, aos)"
    )

    if "uq_contact_pass" not in existing_uniques:
        with op.batch_alter_table("contacts") as batch:
            batch.create_unique_constraint("uq_contact_pass", ["satellite", "aos"])


def downgrade() -> None:
    with op.batch_alter_table("contacts") as batch:
        batch.drop_constraint("uq_contact_pass", type_="unique")
        for name in _NEW_COLUMNS:
            batch.drop_column(name)
