"""track supplier material-specific capabilities

Revision ID: b19c4e7a1d62
Revises: e83c1a9d5b24
Create Date: 2026-10-21
"""
from __future__ import annotations
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = 'b19c4e7a1d62'
down_revision: str | Sequence[str] | None = 'e83c1a9d5b24'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.add_column('supplier_capabilities', sa.Column('material_ids', sa.JSON(), nullable=False, server_default='[]'))

def downgrade() -> None:
    op.drop_column('supplier_capabilities', 'material_ids')
