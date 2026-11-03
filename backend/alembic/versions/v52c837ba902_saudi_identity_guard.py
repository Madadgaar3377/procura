"""Guard new phone identities without altering legacy accounts or verification flags.

Metadata validation occurs in the application; this database guard also covers
SQL writers. The existing canonical unique index prevents concurrent duplicates.
"""
from alembic import op
revision = 'v52c837ba902'
down_revision = 'u41b726af891'
branch_labels = None
depends_on = None

def upgrade():
    if op.get_bind().dialect.name != 'postgresql':
        return
    op.execute("""
    CREATE FUNCTION procura_saudi_identity_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'INSERT' OR NEW.phone IS DISTINCT FROM OLD.phone
         OR NEW.phone_e164 IS DISTINCT FROM OLD.phone_e164 THEN
        IF NEW.phone_e164 !~ '^\+[9][6][6]5[0-9]{8}$' THEN
          RAISE EXCEPTION 'Saudi mobile identity required' USING ERRCODE = '23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER procura_saudi_identity_guard BEFORE INSERT OR UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION procura_saudi_identity_guard();
    """)

def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP TRIGGER procura_saudi_identity_guard ON users')
        op.execute('DROP FUNCTION procura_saudi_identity_guard()')
