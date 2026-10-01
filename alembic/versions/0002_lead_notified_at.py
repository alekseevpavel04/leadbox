"""lead notified_at

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-01 19:10:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.add_column(sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.drop_column("notified_at")
