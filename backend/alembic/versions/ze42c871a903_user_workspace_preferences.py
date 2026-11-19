"""user_workspace_preferences

Revision ID: ze42c871a903
Revises: ze41b760f892
Create Date: 2026-11-17 16:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'ze42c871a903'
down_revision = 'ze41b760f892'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('preferred_role', sa.String(length=20), nullable=True))
    op.add_column('users', sa.Column('last_workspace_role', sa.String(length=20), nullable=True))
    op.add_column('users', sa.Column('last_org_id', sa.String(length=36), nullable=True))
    op.add_column('users', sa.Column('onboarding_completed', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.execute(
        """
        UPDATE users
        SET onboarding_completed = true
        WHERE EXISTS (
            SELECT 1 FROM organization_members m
            JOIN organizations o ON o.id = m.organization_id
            WHERE m.user_id = users.id AND o.is_active = true
        )
        """
    )


def downgrade() -> None:
    op.drop_column('users', 'onboarding_completed')
    op.drop_column('users', 'last_org_id')
    op.drop_column('users', 'last_workspace_role')
    op.drop_column('users', 'preferred_role')
