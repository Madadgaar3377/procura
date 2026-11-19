"""Material queue ordering and guarded active category identities.

Legacy duplicate groups are marked NULL for explicit review, never merged/deleted.
A trigger checks those legacy groups too; the partial unique index protects cleaned
and new identities. After an explicit merge the survivor enters the unique index.
"""
from collections import Counter
import unicodedata
from alembic import op
import sqlalchemy as sa

revision='ze44e093cb25'
down_revision='ze43d982ba14'
branch_labels=None
depends_on=None


def upgrade():
    bind=op.get_bind()
    op.create_index('ix_import_rows_material_cursor','import_rows',['batch_id','row_number','id'])
    if bind.dialect.name!='postgresql':return
    op.execute('CREATE INDEX ix_materials_import_lower_name ON materials (lower(name_en))')
    op.execute("LOCK TABLE categories IN SHARE ROW EXCLUSIVE MODE")
    # Match the normalizer used by all application entry points.
    categories=list(bind.execute(sa.text('SELECT id,parent_id,name_en,active FROM categories')).mappings())
    normalize=lambda value:' '.join(unicodedata.normalize('NFKC',value or '').casefold().split())
    counts=Counter((c['parent_id'],normalize(c['name_en'])) for c in categories if c['active'])
    for c in categories:
        value=normalize(c['name_en'])
        if c['active'] and counts[(c['parent_id'],value)]>1:value=None
        bind.execute(sa.text('UPDATE categories SET normalized_name=:name WHERE id=:id'),{'name':value,'id':c['id']})
    op.execute("CREATE UNIQUE INDEX uq_categories_active_identity ON categories (COALESCE(parent_id,''),normalized_name) WHERE active IS TRUE AND normalized_name IS NOT NULL AND normalized_name<>''")
    op.execute("""
    CREATE FUNCTION guard_category_identity() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE identity_name text;
    BEGIN
      PERFORM pg_advisory_xact_lock(717341395);
      identity_name := COALESCE(NULLIF(NEW.normalized_name,''), lower(regexp_replace(trim(normalize(NEW.name_en,NFKC)), '[[:space:]]+', ' ', 'g')));
      IF NEW.active AND EXISTS (
        SELECT 1 FROM categories c WHERE c.active AND c.id<>NEW.id
          AND c.parent_id IS NOT DISTINCT FROM NEW.parent_id
          AND COALESCE(NULLIF(c.normalized_name,''),lower(regexp_replace(trim(normalize(c.name_en,NFKC)), '[[:space:]]+', ' ', 'g')))=identity_name
      ) THEN
        RAISE EXCEPTION 'Duplicate category identity requires review' USING ERRCODE='23505', CONSTRAINT='uq_categories_active_identity';
      END IF;
      NEW.normalized_name := identity_name;
      RETURN NEW;
    END $$;
    CREATE TRIGGER categories_identity_guard BEFORE INSERT OR UPDATE OF name_en,parent_id,active,normalized_name ON categories
      FOR EACH ROW EXECUTE FUNCTION guard_category_identity();
    """)


def downgrade():
    if op.get_bind().dialect.name=='postgresql':
        op.execute('DROP INDEX IF EXISTS ix_materials_import_lower_name')
        op.execute('DROP TRIGGER IF EXISTS categories_identity_guard ON categories')
        op.execute('DROP FUNCTION IF EXISTS guard_category_identity()')
        op.execute('DROP INDEX IF EXISTS uq_categories_active_identity')
    op.drop_index('ix_import_rows_material_cursor',table_name='import_rows')
