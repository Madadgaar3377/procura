"""Bind durable recovery credentials to accounts and revoke legacy credentials."""
from alembic import op
import sqlalchemy as sa

revision = 'zb18e437c569'
down_revision = 'za07d326b458'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('email_otps', sa.Column('user_id', sa.String(36), nullable=True))
    op.add_column('email_otps', sa.Column('credential_hash', sa.String(64), nullable=True))
    for name in ('verified_at', 'consumed_at', 'revoked_at'):
        op.add_column('email_otps', sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_email_otps_user_id', 'email_otps', ['user_id'])
    op.create_index('ix_email_otps_reset_link_hash', 'email_otps', ['code_hash'],
                    postgresql_where=sa.text("purpose = 'password_reset_link'"),
                    sqlite_where=sa.text("purpose = 'password_reset_link'"))
    if op.get_bind().dialect.name != 'sqlite':
        op.create_foreign_key('fk_email_otps_user_id', 'email_otps', 'users', ['user_id'], ['id'], ondelete='CASCADE')
    op.execute(sa.text("UPDATE email_otps SET used=true, revoked_at=CURRENT_TIMESTAMP "
                       "WHERE purpose IN ('password_reset','password_verified','password_reset_link')"))


def downgrade():
    # Revocations deliberately survive rollback. Never restore issued secrets.
    if op.get_bind().dialect.name != 'sqlite':
        op.drop_constraint('fk_email_otps_user_id', 'email_otps', type_='foreignkey')
    op.drop_index('ix_email_otps_user_id', table_name='email_otps')
    op.drop_index('ix_email_otps_reset_link_hash', table_name='email_otps')
    for name in ('revoked_at','consumed_at','verified_at','credential_hash','user_id'):
        op.drop_column('email_otps', name)
