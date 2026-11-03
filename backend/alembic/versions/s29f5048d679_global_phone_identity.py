"""Canonical global phone identities; abort before writes on unresolved legacy rows.

Raw phone values, verification flags and account ownership are never overwritten.
Run scripts/audit_global_phones.py and review conflicts before applying.
"""
import json
from pathlib import Path

import sqlalchemy as sa

from alembic import context, op

revision = 's29f5048d679'
down_revision = 'r18e4f37c568'
branch_labels = None
depends_on = None


def upgrade():
    import phonenumbers as pn

    from app.identity import normalize_phone

    bind = op.get_bind()
    manifest = context.get_x_argument(as_dictionary=True).get('phone_regions')
    countries = json.loads(Path(manifest).read_text(encoding='utf-8')) if manifest else {}
    if not isinstance(countries, dict):
        raise RuntimeError('Reviewed phone region manifest must map account IDs to ISO region codes')
    if bind.dialect.name == 'postgresql':
        # Prevent an old application writer racing the audit/backfill/index creation.
        bind.execute(sa.text('LOCK TABLE users IN ACCESS EXCLUSIVE MODE'))
    rows = bind.execute(sa.text('SELECT id,phone FROM users')).mappings().all()
    resolved = []
    seen = set()
    for row in rows:
        try:
            canonical = normalize_phone(row['phone'], countries.get(row['id']))
        except (ValueError, TypeError, AttributeError) as exc:
            raise RuntimeError('Global phone migration blocked: invalid or unclear legacy country context; review redacted audit. No accounts changed.') from exc
        if canonical in seen:
            raise RuntimeError('Global phone migration blocked: duplicate normalized identities require review. No accounts changed.')
        seen.add(canonical)
        region = pn.region_code_for_number(pn.parse(canonical, None))
        resolved.append({'id': row['id'], 'canonical': canonical,
                         'country': region if region in pn.SUPPORTED_REGIONS else None})
    op.add_column('users', sa.Column('phone_e164', sa.String(16), nullable=True))
    op.add_column('users', sa.Column('phone_country', sa.String(2), nullable=True))
    for row in resolved:
        bind.execute(sa.text('UPDATE users SET phone_e164=:canonical,phone_country=:country WHERE id=:id'), row)
    with op.batch_alter_table('users') as batch:
        batch.alter_column('phone_e164', existing_type=sa.String(16), nullable=False)
        batch.create_index('ix_users_phone_e164', ['phone_e164'], unique=True)
        batch.drop_index('uq_users_phone_identity')
        if bind.dialect.name == 'postgresql':
            batch.create_check_constraint('ck_users_phone_e164_format', r"phone_e164 ~ '^\+[1-9][0-9]{1,14}$'")
    op.add_column('phone_otps', sa.Column('user_id', sa.String(36), nullable=True))
    op.add_column('phone_otps', sa.Column('previous_phone', sa.String(16), nullable=True))
    op.add_column('phone_otps', sa.Column('credential_hash', sa.String(64), nullable=True))
    with op.batch_alter_table('phone_otps') as batch:
        batch.create_foreign_key('fk_phone_otps_user_id', 'users', ['user_id'], ['id'], ondelete='CASCADE')
        batch.create_index('ix_phone_otps_user_id', ['user_id'])


def downgrade():
    raise RuntimeError('Reviewed rollback required: preserve canonical identity and phone-change evidence.')
