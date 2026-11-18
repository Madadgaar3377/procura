"""RFQ autocomplete contract: exact scope, complete pages and field search."""
import pytest
from sqlalchemy import select
from app.models import Category, Material
from app.schemas import RFQLineIn
from app.modules.rfqs.router import prepare_line
from test_response_cache import cached_api

@pytest.fixture
def reinforcement(cached_api):
    t = cached_api
    with t.db() as db:
        db.add(Category(id='reinforcement', code='REINF', name_en='Reinforcement & Prestressing', active=True))
        db.flush()
        for sub, count, label in [('steel',18,'Reinforcing Steel'),('accessories',8,'Reinforcement Accessories'),('prestressing',2,'Post-Tensioning & Prestressing')]:
            db.add(Category(id=sub, code=sub, name_en=label, parent_id='reinforcement', active=True))
            db.flush()
            for i in range(count):
                db.add(Material(id=f'{sub}-{i}', code=f'{sub}-{i:03}', category_id=sub,
                    main_category_id='reinforcement', subcategory_id=sub,
                    name_en=f'Rebar Deformed ASTM A615 Gr60 diameter {i+10}mm 12m',
                    normalized_description=f'rebar deformed astm a615 gr60 diameter {i+10}mm 12m',
                    description='Specification coated ASTM A775', source_material_code=f'SOURCE-{sub}-{i:03}',
                    unit='M', active=True))
        db.commit()
    return t

@pytest.mark.parametrize('sub,count',[('steel',18),('accessories',8),('prestressing',2)])
def test_all_subcategory_records_accessible_without_duplicates(reinforcement,sub,count):
    client=reinforcement.client
    records=[]
    for offset in range(0,count+7,7):
        page=client.get('/taxonomy/materials',params=dict(main_category_id='reinforcement',subcategory_id=sub,autocomplete=True,limit=7,offset=offset))
        assert page.status_code==200
        records.extend(page.json())
    assert len(records)==count
    assert len({m['id'] for m in records})==count
    assert all(m['subcategory_id']==sub for m in records)
    assert client.get('/taxonomy/materials',params=dict(main_category_id='cat',subcategory_id=sub)).json()==[]

@pytest.mark.parametrize('query,expected',[('SOURCE-steel-017',1),('A775',18),('Gr60 27mm',1),('formed',18),('27mm SOURCE-steel-017',1),('%',0),('_',0)])
def test_autocomplete_searches_partial_size_specification_and_code(reinforcement,query,expected):
    response=reinforcement.client.get('/taxonomy/materials',params=dict(main_category_id='reinforcement',subcategory_id='steel',autocomplete=True,q=query,limit=100))
    assert response.status_code==200
    assert len(response.json())==expected


def test_autocomplete_respects_requested_page_size(reinforcement):
    # Regression: autocomplete silently capped otherwise valid limits at 20.
    response=reinforcement.client.get('/taxonomy/materials',params=dict(main_category_id='reinforcement',autocomplete=True,limit=100))
    assert len(response.json())==28


def test_custom_rfq_description_keeps_material_and_master(reinforcement):
    with reinforcement.db() as db:
        line=RFQLineIn(material_id='steel-0',main_category_id='reinforcement',subcategory_id='steel',description='Custom delivery requirement',unit='M',quantity='2')
        snapshot=prepare_line(db,line)
        assert snapshot['material_id']=='steel-0'
        assert snapshot['description']=='Custom delivery requirement'
        assert db.scalar(select(Material.name_en).where(Material.id=='steel-0')).startswith('Rebar Deformed')


def test_custom_description_matching_uses_material_and_capabilities(reinforcement):
    from datetime import UTC, datetime, timedelta
    from app.models import Capability, Organization, RFQ, RFQItem, Invitation
    from app.modules.matching.service import match_and_invite
    with reinforcement.db() as db:
        buyer=Organization(id='buyer',name='Buyer',name_normalized='buyer',city='Riyadh',is_buyer=True)
        db.add(buyer)
        for supplier,material in [('exact','steel-0'),('different-scope','accessories-0')]:
            db.add(Organization(id=supplier,name=supplier,name_normalized=supplier,city='Riyadh',is_supplier=True,is_active=True,verified_status='verified'))
            db.flush()
            db.add(Capability(org_id=supplier,category_id='reinforcement',material_ids=[material],city='Riyadh',confirmed=True,accepts_rfqs=True,eligibility_status='enabled'))
        db.flush()
        rfq=RFQ(reference='RFQ-MATERIAL-TEST',buyer_org_id=buyer.id,title='Selected steel',city='Riyadh',response_deadline=datetime.now(UTC)+timedelta(days=2),status='published')
        db.add(rfq);db.flush()
        db.add(RFQItem(rfq_id=rfq.id,material_id='steel-0',main_category_id='reinforcement',subcategory_id='steel',description='Buyer customized text',specifications='',unit='M',quantity=2))
        db.flush()
        match_and_invite(db,rfq)
        assert set(db.scalars(select(Invitation.supplier_org_id).where(Invitation.rfq_id==rfq.id)))=={'exact'}
