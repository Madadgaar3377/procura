"""Public business details require field consent for every supplier source."""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Organization, PublicProfileConsent
from app.modules.capabilities.public_details import published_details


def test_imported_admin_and_registered_supplier_public_projection():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for source in ('imported', 'admin', 'registered'):
            db.add(Organization(id=source, name=source, name_normalized=source,
                                source=source, is_supplier=True, is_active=True,
                                verified_status='verified', city='Dammam',
                                description='Pipes; Valves; Fittings' if source == 'imported' else
                                'We provide engineering services across the region.',
                                business_email=f'sales-{source}@example.test',
                                business_phone='+966500001234', website='https://example.test',
                                address='Office 12', country='Saudi Arabia',
                                contact_name='Private employee'))
        db.commit()
        for source in ('imported', 'admin', 'registered'):
            hidden = published_details(db, source)
            assert 'business_email' not in hidden and 'business_phone' not in hidden
            assert 'address' not in hidden and 'contact_name' not in hidden
            assert hidden['website'] == 'https://example.test'
        assert 'description' not in published_details(db, 'imported')
        assert published_details(db, 'registered')['description'].startswith('We provide')
        db.add(PublicProfileConsent(org_id='imported', fields=['business_email', 'business_phone', 'country', 'address']))
        db.add(PublicProfileConsent(org_id='admin', fields=['business_email', 'business_phone']))
        db.add(PublicProfileConsent(org_id='registered', fields=['business_email', 'business_phone']))
        db.commit()
        for source in ('imported', 'admin', 'registered'):
            public = published_details(db, source)
            assert public['business_email'] == f'sales-{source}@example.test'
            assert public['business_phone'] == '+966500001234'
            assert 'contact_name' not in public
        assert published_details(db, 'imported')['address'] == 'Office 12'
        assert 'address' not in published_details(db, 'registered')
