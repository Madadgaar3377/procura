"""durable public contact inquiries

Revision ID: f2a8c14d6e90
Revises: c2a1b7345e6f
Create Date: 2026-10-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2a8c14d6e90'
down_revision: Union[str, Sequence[str], None] = 'c2a1b7345e6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'contact_inquiries',
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('email', sa.String(length=200), nullable=False),
        sa.Column('company', sa.String(length=220), nullable=True),
        sa.Column('phone', sa.String(length=30), nullable=True),
        sa.Column('topic', sa.String(length=40), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('handled_by', sa.String(length=36), nullable=True),
        sa.Column('handled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['handled_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_contact_inquiries_email', 'contact_inquiries', ['email'], unique=False)
    op.create_index('ix_contact_inquiries_status', 'contact_inquiries', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_contact_inquiries_status', table_name='contact_inquiries')
    op.drop_index('ix_contact_inquiries_email', table_name='contact_inquiries')
    op.drop_table('contact_inquiries')
