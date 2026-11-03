"""Read-only configured PostgreSQL identity audit; output contains no contact values."""

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, text

from app.config import get_settings, normalize_database_url
from app.identity import normalize_email, normalize_phone


def reference(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def audit_rows(rows):
    groups = {"email": defaultdict(list), "phone": defaultdict(list)}
    invalid = {"email": [], "phone": []}
    missing = {"email": [], "phone": [], "username": []}
    noncanonical = {"email": [], "phone": []}
    usernames = defaultdict(list)
    email_usernames = []
    normalized = []
    for row in rows:
        ref = reference(row["id"])
        username = (row["username"] or "").strip().casefold()
        usernames[username].append(ref)
        if "@" in username:
            email_usernames.append(ref)
        values = {}
        for field, normalizer in [("email", normalize_email), ("phone", normalize_phone)]:
            raw = row[field]
            if not raw:
                missing[field].append(ref)
                continue
            try:
                value = normalizer(raw)
            except ValueError:
                invalid[field].append(ref)
                continue
            groups[field][value].append(ref)
            values[field] = value
            if raw != value:
                noncanonical[field].append(ref)
        if not username:
            missing["username"].append(ref)
        normalized.append((ref, values))
    collisions = []
    for ref, values in normalized:
        for other in usernames.get(values.get("email"), []):
            if ref != other:
                collisions.append({"email_owner": ref, "username_owner": other})
    return {
        "users_scanned": len(rows),
        "missing": missing,
        "invalid": invalid,
        "noncanonical": noncanonical,
        "email_like_usernames": email_usernames,
        "username_email_collisions": collisions,
        "duplicates": {
            field: [
                {"identity_fingerprint": reference(identity), "users": refs}
                for identity, refs in values.items()
                if len(refs) > 1
            ]
            for field, values in groups.items()
        },
    }


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    url = normalize_database_url(get_settings().database_url)
    if not url.startswith("postgresql"):
        print("Hosted audit not run: configured database is not PostgreSQL.")
        return 2
    engine = create_engine(
        url,
        connect_args={
            "connect_timeout": 5,
            "options": "-c default_transaction_read_only=on -c statement_timeout=15000",
        },
    )
    try:
        with engine.connect() as conn, conn.begin():
            conn.execute(text("SET TRANSACTION READ ONLY"))
            readonly = conn.scalar(text("SHOW transaction_read_only"))
            if readonly != "on":
                raise RuntimeError("Read-only transaction required")
            rows = conn.execute(text("SELECT id,username,email,phone FROM public.users")).mappings().all()
            result = audit_rows(rows)
            result["transaction_read_only"] = True
            result["constraints"] = [
                dict(r)
                for r in conn.execute(
                    text(
                        "SELECT conname,contype,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid='public.users'::regclass"
                    )
                ).mappings()
            ]
            result["indexes"] = [
                dict(r)
                for r in conn.execute(
                    text(
                        "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='users'"
                    )
                ).mappings()
            ]
            result["missing_password_count"] = conn.scalar(
                text("SELECT count(*) FROM public.users WHERE password_hash IS NULL OR password_hash=''")
            )
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "read_only": True,
                    "users_scanned": result["users_scanned"],
                    "duplicate_email_groups": len(result["duplicates"]["email"]),
                    "duplicate_phone_groups": len(result["duplicates"]["phone"]),
                    "username_email_collisions": len(result["username_email_collisions"]),
                    "report_written": True,
                }
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - driver messages can contain credentials
        # Driver exceptions may contain a connection string; never print them.
        print(
            json.dumps(
                {
                    "audit_completed": False,
                    "error_type": type(exc).__name__,
                    "message": "Read-only database audit could not complete; no changes were made.",
                }
            )
        )
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(run())
