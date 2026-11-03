import argparse
from sqlalchemy import select
from app.database import SessionLocal,Base,engine
from app.models import User,MembershipPlan,Category,Material
from app.security import password_hash
from app.config import get_settings

def require_local_sqlite(operation):
    if get_settings().is_production:
        raise SystemExit(f'{operation} is forbidden in production')
    if engine.dialect.name!='sqlite':
        raise SystemExit(f'{operation} only supports local SQLite; use reviewed Alembic migrations for PostgreSQL')

def init_db():
    require_local_sqlite('init-db')
    # Local quickstart only. PostgreSQL schema changes require reviewed Alembic migrations.
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if not db.scalar(select(MembershipPlan.id)):
            db.add_all([MembershipPlan(name='Starter',quote_limit=10,team_limit=1),MembershipPlan(name='Growth',quote_limit=100,auto_drafts=True,team_limit=3),MembershipPlan(name='Business',quote_limit=1000,auto_drafts=True,team_limit=10)])
        db.commit()
    print('Local development schema and plan defaults are ready')

def admin_create(username,phone,password):
    if len(password)<6:raise SystemExit('Admin password must contain at least 6 characters')
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.username==username.casefold())):raise SystemExit('User already exists')
        user=User(username=username.casefold(),phone=phone,password_hash=password_hash(password),phone_verified=True,is_admin=True)
        db.add(user);db.commit()
    print(f'Admin {username} created. Use /api/v1/auth/login.')

def seed_demo():
    require_local_sqlite('seed-demo')
    sample=[('CCTV','CCTV & Security','كاميرات المراقبة','CAM-5MP-IP68','Outdoor 5MP IP camera','No.'),('HVAC','Heating Ventilation Air Conditioning','التكييف','HVAC-AC-01','Air conditioning unit','Set'),('ELEC','Electrical Equipment','معدات كهربائية','CABLE-01','Electrical power cable','M')]
    with SessionLocal() as db:
        for category_code,category_name,arabic,code,name,unit in sample:
            category=db.scalar(select(Category).where(Category.code==category_code))
            if not category:
                category=Category(code=category_code,name_en=category_name,name_ar=arabic)
                db.add(category);db.flush()
            if not db.scalar(select(Material).where(Material.code==code)):
                db.add(Material(code=code,category_id=category.id,name_en=name,unit=unit))
        db.commit()
    print('Three fictional material examples seeded; no real suppliers or customer contacts added')

def main():
    p=argparse.ArgumentParser(description='Saudi B2B development CLI')
    sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('init-db')
    sub.add_parser('seed-demo')
    admin=sub.add_parser('create-admin');admin.add_argument('--username',required=True);admin.add_argument('--phone',required=True)
    args=p.parse_args()
    if args.cmd=='init-db':init_db()
    elif args.cmd=='seed-demo':seed_demo()
    elif args.cmd=='create-admin':
        import getpass
        admin_create(args.username,args.phone,getpass.getpass('Admin password: '))
if __name__=='__main__':main()
