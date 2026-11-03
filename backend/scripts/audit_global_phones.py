"""Read-only global phone audit. No phone values, usernames or credentials in reports."""
import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text

from app.config import get_settings, normalize_database_url
from app.identity import normalize_phone


def reference(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def audit_rows(rows, countries=None):
    import phonenumbers as pn
    countries = countries or {}
    groups = defaultdict(list)
    issues = []
    kinds = defaultdict(int)
    verified = defaultdict(list)
    for row in rows:
        raw = (row['phone'] or '').strip()
        ref = reference(row['id'])
        country = countries.get(row['id']) or row.get('phone_country')
        kind = 'international' if raw.startswith('+') else 'national_or_unclear'
        kinds[kind] += 1
        if kind == 'national_or_unclear' and not country:
            issues.append({'user_reference': ref, 'issue': 'country_context_required'})
            continue
        try:
            canonical = normalize_phone(raw, country)
        except ValueError as exc:
            issues.append({'user_reference': ref, 'issue': str(exc)})
            continue
        groups[canonical].append(ref)
        parsed = pn.parse(canonical, None)
        category = 'valid_saudi_mobile' if parsed.country_code == 966 and pn.number_type(parsed) in (pn.PhoneNumberType.MOBILE, pn.PhoneNumberType.FIXED_LINE_OR_MOBILE) else 'non_saudi' if parsed.country_code != 966 else 'saudi_not_mobile'
        kinds[category] += 1
        if category != 'valid_saudi_mobile':
            issues.append({'user_reference': ref, 'issue': category + '_manual_transition_required'})
        if row.get('phone_verified'):
            verified[canonical].append(ref)
        if raw != canonical:
            issues.append({'user_reference': ref, 'issue': 'noncanonical_representation'})
    duplicates = [{'identity_reference': reference(key), 'users': refs,
                   'verified_users': verified[key]} for key, refs in groups.items() if len(refs) > 1]
    return {'metadata_version': pn.__version__, 'supported_regions': sorted(pn.SUPPORTED_REGIONS),
            'supported_region_count': len(pn.SUPPORTED_REGIONS), 'users_scanned': len(rows),
            'formats': dict(kinds), 'issues': issues, 'duplicates': duplicates,
            'duplicate_verified_identity_groups': sum(len(item['verified_users']) > 1 for item in duplicates),
            'unresolved_region_count': sum(item['issue'] == 'country_context_required' for item in issues)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--regions', type=Path, help='Reviewed JSON user-ID to ISO region mapping; never inferred')
    args = parser.parse_args()
    countries = json.loads(args.regions.read_text()) if args.regions else {}
    engine = create_engine(normalize_database_url(get_settings().database_url),
                           connect_args={'connect_timeout': 5})
    try:
        if engine.dialect.name != 'postgresql':
            raise RuntimeError('Configured PostgreSQL required; audit was not run')
        with engine.connect() as conn, conn.begin():
            conn.execute(text('SET TRANSACTION READ ONLY'))
            conn.execute(text("SET LOCAL statement_timeout = '15s'"))
            if conn.scalar(text('SHOW transaction_read_only')) != 'on':
                raise RuntimeError('Read-only transaction required')
            columns = conn.execute(text("SELECT column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema='public' AND table_name='users'")).mappings().all()
            rows = conn.execute(text('SELECT id,phone,phone_verified FROM public.users')).mappings().all()
            result = audit_rows([dict(row) for row in rows], countries)
            result['schema'] = [dict(col) for col in columns if col['column_name'].startswith('phone')]
            result['indexes'] = [dict(row) for row in conn.execute(text("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='users'")).mappings()]
            result['transaction_read_only'] = True
        args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({key: result[key] for key in ('users_scanned', 'supported_region_count', 'unresolved_region_count', 'duplicate_verified_identity_groups')}))
        return 0
    except Exception as exc:  # noqa: BLE001 - database driver messages may expose credentials
        failure={'audit_completed': False, 'error_type': type(exc).__name__, 'no_changes_made': True, 'duplicate_identities': 'unknown: read-only audit did not complete'}
        args.output.write_text(json.dumps(failure,indent=2),encoding='utf-8')
        print(json.dumps(failure))
        return 1
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
