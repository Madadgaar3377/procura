from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Organization
from app.profile_schema import compatible_organization_reads


def test_organization_reads_work_before_profile_completion_migration():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    event.listen(sessions.class_, "do_orm_execute", compatible_organization_reads)
    try:
        with sessions() as db:
            company = Organization(name="Legacy Supplier", name_normalized="legacy supplier", is_supplier=True, is_buyer=False)
            db.add(company); db.commit()
            company_id = company.id
            db.execute(text("DROP INDEX ix_organizations_profile_status"))
            db.execute(text("ALTER TABLE organizations DROP COLUMN profile_status"))
            db.commit()

        with sessions() as db:
            company = db.scalar(select(Organization).where(Organization.id == company_id))
            assert company is not None
            assert company.name == "Legacy Supplier"
            assert company.profile_status is None
    finally:
        engine.dispose()
