"""Operator-only phone repair across the pre/post canonical-phone schema.

No account deletion or verification bypass. Dry run by default; --apply writes.
"""
import argparse
from datetime import UTC, datetime
from getpass import getpass
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa
from app.identity import normalize_saudi_phone


def repair(connection, user_id, replacement, apply=False):
    """Caller owns transaction. Return only non-sensitive outcome metadata."""
    canonical = normalize_saudi_phone(replacement, sms=True)
    metadata = sa.MetaData()
    users = sa.Table('users', metadata, autoload_with=connection)
    # Serialize repairs against account changes, including duplicate checks.
    if connection.dialect.name == 'postgresql':
        connection.execute(sa.text('LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE'))
    target = connection.execute(sa.select(users).where(users.c.id == user_id).with_for_update()).mappings().one_or_none()
    if not target:
        raise ValueError('Account ID not found; nothing changed.')
    if target['phone'] == canonical:
        raise ValueError('The account already has that number; nothing changed.')
    for other in connection.execute(sa.select(users.c.id, users.c.phone).where(users.c.id != user_id)).mappings():
        try:
            existing = normalize_saudi_phone(other['phone'])
        except ValueError:
            existing = other['phone']
        if existing == canonical:
            raise ValueError('Replacement belongs to another account; nothing changed.')
    if not apply:
        return {'validated': True, 'applied': False, 'phone_verification_required': True}
    values = {'phone': canonical, 'phone_verified': False}
    if 'phone_e164' in users.c:
        import phonenumbers as pn
        values['phone_e164'] = canonical
        values['phone_country'] = pn.region_code_for_number(pn.parse(canonical, None))
    if 'sms_notifications_enabled' in users.c:
        values['sms_notifications_enabled'] = False
    connection.execute(users.update().where(users.c.id == user_id).values(**values))
    sessions = sa.Table('refresh_sessions', metadata, autoload_with=connection)
    connection.execute(sessions.update().where(sessions.c.user_id == user_id).values(revoked=True))
    otps = sa.Table('phone_otps', metadata, autoload_with=connection)
    match = otps.c.phone.in_([target['phone'], canonical])
    if 'user_id' in otps.c:
        match = sa.or_(match, otps.c.user_id == user_id)
    connection.execute(otps.update().where(match).values(used=True))
    audit = sa.Table('audit_events', metadata, autoload_with=connection)
    connection.execute(audit.insert().values(id=str(uuid4()), created_at=datetime.now(UTC),
        actor_user_id=None, org_id=None, action='auth.operator_phone_repaired', entity_id=user_id,
        metadata_json={'source':'local_operator', 'phone_verification_reset':True, 'sessions_revoked':True}))
    return {'validated':True, 'applied':True, 'phone_verification_required':True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-id', required=True)
    parser.add_argument('--apply', action='store_true', help='Commit the validated repair; otherwise roll back.')
    args = parser.parse_args()
    engine = None
    try:
        from app.config import get_settings, normalize_database_url
        replacement = getpass('Correct mobile number with + and country code (hidden): ')
        engine = sa.create_engine(normalize_database_url(get_settings().database_url),
                                  connect_args={'connect_timeout':5})
        with engine.connect() as connection:
            with connection.begin() as transaction:
                result = repair(connection, args.user_id, replacement, args.apply)
                if not args.apply:
                    transaction.rollback()
        print('Repair saved. Verify the replacement phone through OTP after migrations.' if result['applied']
              else 'Validation passed. No changes made. Re-run with --apply to save.')
        return 0
    except ValueError as exc:
        print(str(exc))
        return 1
    except Exception as exc:
        # Never expose driver parameters, credentials, phone numbers or hashes.
        print(f'Repair failed ({type(exc).__name__}). Transaction rolled back; no changes saved.')
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
