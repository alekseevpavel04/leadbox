"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-01 18:02:27.340797
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Self-contained on purpose: no imports from leadbox.models, so this file keeps meaning the same
# schema after the models change. Enums are stored as their string values, timestamps as timestamptz.
ENUM = sa.String(length=16)
TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("contact", sa.Text(), nullable=True),
        sa.Column("request", sa.Text(), nullable=True),
        sa.Column("source", ENUM, nullable=False),
        sa.Column("campaign", sa.String(length=64), nullable=True),
        sa.Column("status", ENUM, nullable=False),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=True),
        sa.Column("tg_username", sa.String(length=64), nullable=True),
        sa.Column("form_step", ENUM, nullable=True),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.Column("first_response_at", TIMESTAMP, nullable=True),
        sa.Column("updated_at", TIMESTAMP, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leads")),
    )
    op.create_index(op.f("ix_leads_tg_user_id"), "leads", ["tg_user_id"])

    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tags")),
        sa.UniqueConstraint("name", name=op.f("uq_tags_name")),
    )

    op.create_table(
        "lead_tags",
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], name=op.f("fk_lead_tags_lead_id_leads"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], name=op.f("fk_lead_tags_tag_id_tags"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("lead_id", "tag_id", name=op.f("pk_lead_tags")),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("direction", ENUM, nullable=False),
        sa.Column("channel", ENUM, nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("sent_at", TIMESTAMP, nullable=False),
        sa.Column("tg_message_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], name=op.f("fk_messages_lead_id_leads"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index(op.f("ix_messages_lead_id"), "messages", ["lead_id"])

    op.create_table(
        "business_connections",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", TIMESTAMP, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_connections")),
    )

    op.create_table(
        "processed_updates",
        sa.Column("update_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("processed_at", TIMESTAMP, nullable=False),
        sa.PrimaryKeyConstraint("update_id", name=op.f("pk_processed_updates")),
    )


def downgrade() -> None:
    op.drop_table("processed_updates")
    op.drop_table("business_connections")
    op.drop_index(op.f("ix_messages_lead_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_table("lead_tags")
    op.drop_table("tags")
    op.drop_index(op.f("ix_leads_tg_user_id"), table_name="leads")
    op.drop_table("leads")
