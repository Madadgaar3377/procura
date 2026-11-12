"""Exercise real RFQ data through API routes, entirely inside an outer rollback.

Temporary QA memberships and a clearly synthetic quote never commit. Providers,
background workers, and real account sign-in are not exercised.
"""
import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.database import engine, get_db
from app.models import Invitation, Member, RFQ, RFQItem, User
from app.modules.capabilities.directory import supplier_products
from app.modules.matching.router import router as matching_router
from app.modules.matching.service import match_and_invite
from app.modules.quotations.router import router as quotation_router
from app.security import current_user
from correct_rfq_matching import reclassify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rfq', required=True)
    parser.add_argument('--supplier', required=True)
    parser.add_argument('--repair-material', required=True)
    parser.add_argument('--subcategory', required=True)
    args = parser.parse_args()
    with engine.connect() as connection:
        transaction = connection.begin()
        db = Session(bind=connection, join_transaction_mode='create_savepoint', expire_on_commit=False)
        try:
            rfq = db.get(RFQ, args.rfq)
            assert rfq, 'RFQ not found'
            reclassify(db, args.repair_material, args.subcategory)
            db.flush()
            result = match_and_invite(db, rfq)
            db.flush()
            assert match_and_invite(db, rfq)['new_invitations'] == 0
            invitation = db.scalar(select(Invitation).where(Invitation.rfq_id == rfq.id, Invitation.supplier_org_id == args.supplier))
            assert invitation, 'Expected supplier is not eligible'
            existing_members = db.scalar(select(func.count()).select_from(Member).where(Member.organization_id == args.supplier))
            product_page = supplier_products(args.supplier, db, q='', main_category_id=None, subcategory_id=None, page=1, limit=100)
            assert any(row['id'] == args.repair_material and row['subcategory_id'] == args.subcategory for row in product_page['items'])
            users = []
            for org in [rfq.buyer_org_id, args.supplier]:
                token = uuid4().hex
                user = User(username='rfq-rollback-qa-' + token, phone='+9665' + str(int(token[:8], 16) % 100000000).zfill(8),
                            password_hash='disabled-rollback-only', is_active=True)
                db.add(user); db.flush()
                db.add(Member(user_id=user.id, organization_id=org, role='owner'))
                users.append(user)
            db.flush()
            app = FastAPI()
            app.include_router(matching_router, prefix='/api/v1')
            app.include_router(quotation_router, prefix='/api/v1')
            def dependency():
                yield db
            app.dependency_overrides[get_db] = dependency
            app.dependency_overrides[current_user] = lambda: users[1]
            client = TestClient(app)
            def check(response):
                assert response.status_code == 200, f'{response.status_code}: {response.text[:300]}'
                return response.json()
            invitations = check(client.get('/api/v1/matching/invitations'))
            assert any(row['id'] == invitation.id for row in invitations)
            check(client.post(f'/api/v1/matching/invitations/{invitation.id}/accept'))
            draft = check(client.post(f'/api/v1/quotations/invitations/{invitation.id}/auto-draft'))
            quote_id = draft['quotation']['id']
            item = db.scalar(select(RFQItem).where(RFQItem.rfq_id == rfq.id))
            check(client.put(f'/api/v1/quotations/{quote_id}/lines', json={'lines': [{
                'rfq_item_id': item.id, 'quantity': str(item.quantity), 'unit_price': '1.00', 'lead_days': 7,
                'brand': 'ABB', 'compliance': 'unconfirmed', 'note': 'ROLLBACK QA — not a commercial quotation'}]}))
            check(client.post(f'/api/v1/quotations/{quote_id}/submit'))
            app.dependency_overrides[current_user] = lambda: users[0]
            quote = check(client.get(f'/api/v1/quotations/{quote_id}'))
            assert quote['quotation']['status'] == 'submitted'
            comparison = check(client.get(f'/api/v1/quotations/rfqs/{rfq.id}/comparison'))
            assert quote_id in json.dumps(comparison)
            matching = check(client.get(f'/api/v1/matching/rfqs/{rfq.id}/results'))
            assert matching['matched_suppliers'] == result['eligible_suppliers']
            print(json.dumps({'reference': rfq.reference, 'eligible_suppliers': result['eligible_suppliers'],
                'supplier_existing_members': existing_members, 'public_product_count': product_page['total'],
                'invitation_visible': True, 'accept': 'passed', 'draft_edit_submit': 'passed',
                'buyer_view_compare': 'passed', 'buyer_results': 'passed',
                'rerun_new_invitations': 0, 'retained_changes': 0,
                'scope': 'real PostgreSQL RFQ, temporary QA identities, synthetic quotation, all rolled back'}))
        finally:
            db.close()
            transaction.rollback()
            print('Outer transaction rolled back. No invitations, users, memberships, quotes or notifications committed.')


if __name__ == '__main__':
    main()
