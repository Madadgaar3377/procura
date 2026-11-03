"""Non-destructive normalized identity indexes; abort on conflicts, never remediate.

Revision ID: g91a02b41c77
Revises: f8c12a4d9b60
"""

import sqlalchemy as sa
from sqlalchemy.sql.expression import Grouping

from alembic import op

revision = "g91a02b41c77"
down_revision = "f8c12a4d9b60"
branch_labels = None
depends_on = None


def phone_expression(column):
    value = sa.func.trim(column)
    for separator in (" ", "-", "(", ")", ".", "\t", "\r", "\n"):
        value = sa.func.replace(value, separator, "")
    # Keep explicit grouping for PostgreSQL expression-index DDL.
    return Grouping(sa.case(
        (sa.func.substr(value, 1, 2) == "00", "+" + sa.func.substr(value, 3)),
        (
            (sa.func.length(value) == 10) & (sa.func.substr(value, 1, 2).in_(["05", "01"])),
            "+966" + sa.func.substr(value, 2),
        ),
        ((sa.func.length(value) == 9) & (sa.func.substr(value, 1, 1) == "5"), "+966" + value),
        (
            (sa.func.length(value) == 11) & (sa.func.substr(value, 1, 2) == "03"),
            "+92" + sa.func.substr(value, 2),
        ),
        ((sa.func.length(value) == 12) & (sa.func.substr(value, 1, 3) == "966"), "+" + value),
        ((sa.func.length(value) == 12) & (sa.func.substr(value, 1, 2) == "92"), "+" + value),
        else_=value,
    ))


def upgrade():
    bind = op.get_bind()
    from app.identity import normalize_email, normalize_phone

    users = sa.table("users", sa.column("id"), sa.column("email"), sa.column("phone"), sa.column("username"))
    expressions = {
        "uq_users_email_identity": sa.func.lower(sa.func.trim(users.c.email)),
        "uq_users_phone_identity": phone_expression(users.c.phone),
    }
    rows = bind.execute(
        sa.select(
            users.c.email,
            users.c.phone,
            expressions["uq_users_phone_identity"].label("phone_key"),
            expressions["uq_users_email_identity"].label("email_key"),
        )
    ).mappings()
    for row in rows:
        try:
            if row["email"] is not None and normalize_email(row["email"]) != row["email_key"]:
                raise ValueError("Email representation requires review")
            if normalize_phone(row["phone"]) != row["phone_key"]:
                raise ValueError("Phone representation requires review")
        except (ValueError, TypeError, AttributeError) as exc:
            raise RuntimeError(
                "Invalid or unsupported legacy identities require reviewed remediation; no records changed"
            ) from exc
    other = users.alias("other_user")
    collision = (
        sa.select(users.c.id)
        .select_from(users.join(other, users.c.id != other.c.id))
        .where(sa.func.lower(sa.func.trim(users.c.email)) == sa.func.lower(sa.func.trim(other.c.username)))
        .limit(1)
    )
    if bind.execute(collision).first():
        raise RuntimeError("Username/email collisions require reviewed remediation; no records changed")
    for name, expression in expressions.items():
        conflict = bind.execute(
            sa.select(sa.func.count())
            .select_from(users)
            .where(expression.is_not(None))
            .group_by(expression)
            .having(sa.func.count() > 1)
            .limit(1)
        ).first()
        if conflict:
            raise RuntimeError(
                f"{name}: conflicting identities require reviewed remediation; no records changed"
            )
    for name, expression in expressions.items():
        op.create_index(name, "users", [expression], unique=True)
    if bind.dialect.name == "postgresql":
        # Existing @ usernames remain untouched; new/changed usernames cannot use @.
        # A legacy username cannot be reused as another account's email.
        op.execute(r"""CREATE FUNCTION procura_guard_user_identifiers() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (TG_OP='INSERT' OR NEW.email IS DISTINCT FROM OLD.email) AND NEW.email IS NOT NULL AND
             (NEW.email<>lower(btrim(NEW.email)) OR octet_length(NEW.email)<>char_length(NEW.email)) THEN
            RAISE EXCEPTION 'Email must be normalized' USING ERRCODE='23514';
          END IF;
          IF (TG_OP='INSERT' OR NEW.phone IS DISTINCT FROM OLD.phone) AND NEW.phone !~ '^\+[1-9][0-9]{7,14}$' THEN
            RAISE EXCEPTION 'Phone must be E.164' USING ERRCODE='23514';
          END IF;
          IF (TG_OP='INSERT' OR NEW.username IS DISTINCT FROM OLD.username) AND position('@' in NEW.username)>0 THEN
            RAISE EXCEPTION 'Username cannot contain @' USING ERRCODE='23514';
          END IF;
          IF NEW.email IS NOT NULL AND EXISTS (SELECT 1 FROM public.users u WHERE u.id<>NEW.id AND lower(btrim(u.username))=lower(btrim(NEW.email))) THEN
            RAISE EXCEPTION 'Identity is already registered' USING ERRCODE='23505';
          END IF;
          RETURN NEW;
        END $$""")
        op.execute(
            "CREATE TRIGGER procura_guard_user_identifiers BEFORE INSERT OR UPDATE OF username,email,phone ON public.users FOR EACH ROW EXECUTE FUNCTION procura_guard_user_identifiers()"
        )


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER procura_guard_user_identifiers ON public.users")
        op.execute("DROP FUNCTION procura_guard_user_identifiers()")
    op.drop_index("uq_users_phone_identity", table_name="users")
    op.drop_index("uq_users_email_identity", table_name="users")
