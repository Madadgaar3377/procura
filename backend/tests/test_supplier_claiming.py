import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.database import Base, get_db
from app.models import (
    AuditEvent,
    Capability,
    Category,
    CompanyClaim,
    Contact,
    ContactCapability,
    ContactChannel,
    Member,
    Notification,
    Organization,
    User,
)
from app.modules.organizations.router import router
from app.security import admin_user, current_user


def _setup():
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(router)

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    settings = get_settings()
    old_upload_dir = settings.upload_dir
    old_r2_enabled = settings.r2_enabled
    tmp_dir = Path('.test-runtime') / f'claim-test-{uuid.uuid4().hex}'
    tmp_dir.mkdir(parents=True, exist_ok=True)
    settings.upload_dir = str(tmp_dir)
    settings.r2_enabled = False
    return engine, sessions, app, settings, (old_upload_dir, old_r2_enabled), tmp_dir


def test_supplier_search_claim_document_approval_and_data_preservation():
    engine, sessions, app, settings, old_upload_dir, tmp_dir = _setup()
    try:
        with sessions() as db:
            claimant = User(
                username='aljazira_rep',
                phone='+966501112233',
                phone_e164='+966501112233',
                phone_verified=True,
                email='rep@personalmail.example',
                email_verified=True,
                password_hash='unused',
            )
            admin = User(
                username='platform_admin',
                phone='+966509999999',
                phone_e164='+966509999999',
                phone_verified=True,
                email='admin@procumaterial.com',
                email_verified=True,
                is_admin=True,
                password_hash='unused',
            )
            org = Organization(
                name='Al Jazira Steel & Concrete Co.',
                name_normalized='al jazira steel & concrete co.',
                cr_number='1010555444',
                city='Riyadh',
                business_email='sales@aljazira-steel.sa',
                business_phone='+966501112233',
                source='imported',
                is_supplier=True,
                is_buyer=False,
                verified_status='verified',
            )
            unrelated_org = Organization(
                name='Unrelated Supplier Co.',
                name_normalized='unrelated supplier co.',
                source='imported',
                is_supplier=True,
                verified_status='verified',
            )
            cat = Category(code='STEEL', name_en='Structural Steel', normalized_name='structural steel')
            db.add_all([claimant, admin, org, unrelated_org, cat])
            db.flush()

            contact = Contact(
                org_id=org.id,
                name='Faisal Al-Harbi',
                email='sales@aljazira-steel.sa',
                phone='+966501112233',
                phone_normalized='+966501112233',
                is_primary=True,
            )
            db.add(contact)
            db.flush()

            db.add(ContactChannel(
                contact_id=contact.id,
                org_id=org.id,
                kind='phone',
                value='+966501112233',
                normalized_value='+966501112233',
                is_primary=True,
            ))
            cap = Capability(
                org_id=org.id,
                category_id=cat.id,
                city='Riyadh',
                material_ids=['mat-1'],
                mapping_source='supplier_import',
                confirmed=True,
            )
            db.add(cap)
            db.flush()

            db.add(ContactCapability(
                contact_id=contact.id,
                capability_id=cap.id,
                material_ids=['mat-1'],
                is_primary=True,
            ))
            db.commit()

            claimant_id, admin_id, org_id, contact_id = claimant.id, admin.id, org.id, contact.id

        with TestClient(app) as client:
            app.dependency_overrides[current_user] = lambda: User(
                id=claimant_id, phone_e164='+966501112233', phone_verified=True,
                email='rep@personalmail.example', email_verified=True,
            )

            # 1. Search returns safe public fields only (no private email/phone/CR leaked)
            res = client.get('/organizations/claimable/search', params={'q': 'Al Jazira', 'cr_number': '1010555444'})
            assert res.status_code == 200, res.text
            matches = res.json()
            assert len(matches) == 1
            assert matches[0]['id'] == org_id
            assert matches[0]['has_owner'] is False
            assert set(matches[0]['match_reasons']) >= {'name', 'cr_number', 'phone'}
            assert 'business_email' not in matches[0]
            assert 'business_phone' not in matches[0]
            assert 'cr_number' not in matches[0]

            # 2. Matching phone alone does NOT grant access to the organization
            assert client.get(f'/organizations/{org_id}').status_code in (403, 404)
            assert client.get('/organizations').json() == []

            # 3. Upload supporting PDF document
            upload_res = client.post(
                f'/organizations/{org_id}/claims/documents',
                files={'file': ('cr-certificate.pdf', b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\n', 'application/pdf')},
            )
            assert upload_res.status_code == 200, upload_res.text
            doc_key = upload_res.json()['document_key']

            # 4. Submit claim (even though registered with a different personal email)
            claim_res = client.post(
                f'/organizations/{org_id}/claims',
                json={
                    'evidence': 'I am the authorized procurement manager for Al Jazira Steel & Concrete Co.',
                    'document_key': doc_key,
                    'cr_number': '1010555444',
                },
            )
            assert claim_res.status_code == 200, claim_res.text
            claim_id = claim_res.json()['id']
            assert claim_res.json()['status'] == 'pending'
            assert claim_res.json()['has_existing_owner'] is False
            assert 'cr_number' in claim_res.json()['matched_by']

            # Duplicate pending claim by same user is blocked
            dup_res = client.post(
                f'/organizations/{org_id}/claims',
                json={'evidence': 'Duplicate request attempt for the same supplier company.'},
            )
            assert dup_res.status_code == 409

            # Non-admin cannot access admin review queue or claim document
            assert client.get('/organizations/admin/review/queue').status_code == 403
            assert client.get(f'/organizations/admin/review/claim/{claim_id}/document-url').status_code == 403

            # 5. Admin reviews document and approves claim
            app.dependency_overrides[admin_user] = lambda: User(id=admin_id, is_admin=True)
            app.dependency_overrides[current_user] = lambda: User(id=admin_id, is_admin=True)

            queue_res = client.get('/organizations/admin/review/queue')
            assert queue_res.status_code == 200
            assert len(queue_res.json()['claims']) == 1
            assert queue_res.json()['claims'][0]['org_name'] == 'Al Jazira Steel & Concrete Co.'
            assert queue_res.json()['claims'][0]['claimant_username'] == 'aljazira_rep'

            doc_dl = client.get(f'/organizations/admin/review/claim/{claim_id}/document-url')
            assert doc_dl.status_code == 200

            decide_res = client.post(
                f'/organizations/admin/review/claim/{claim_id}',
                json={'approve': True, 'notes': 'Verified CR certificate and authorization letter.'},
            )
            assert decide_res.status_code == 200, decide_res.text
            assert decide_res.json()['status'] == 'approved'
            assert decide_res.json()['assigned_role'] == 'owner'
            assert decide_res.json()['reviewer_notes'] == 'Verified CR certificate and authorization letter.'

            # 6. Claimant now has access to the existing supplier organization
            app.dependency_overrides[current_user] = lambda: User(
                id=claimant_id, phone_e164='+966501112233', phone_verified=True,
                email='rep@personalmail.example', email_verified=True,
            )
            orgs_res = client.get('/organizations')
            assert orgs_res.status_code == 200
            assert len(orgs_res.json()) == 1
            assert orgs_res.json()[0]['id'] == org_id

            mine_res = client.get('/organizations/claims/mine')
            assert mine_res.status_code == 200
            assert mine_res.json()[0]['status'] == 'approved'

        # 7. Verify imported data, contacts, capabilities, and material-to-contact assignments are preserved
        with sessions() as db:
            org_after = db.get(Organization, org_id)
            assert org_after.name == 'Al Jazira Steel & Concrete Co.'
            assert org_after.business_email == 'sales@aljazira-steel.sa'
            assert db.scalar(select(func.count()).select_from(Organization)) == 2
            assert db.scalar(select(func.count()).select_from(Member).where(Member.organization_id == org_id)) == 1
            assert db.scalar(select(func.count()).select_from(Contact).where(Contact.org_id == org_id)) == 1
            assert db.scalar(select(func.count()).select_from(Capability).where(Capability.org_id == org_id)) == 1
            assert db.scalar(select(func.count()).select_from(ContactCapability).where(ContactCapability.contact_id == contact_id)) == 1
            # Only claimant notified, never broadcast to unrelated suppliers
            notifs = db.scalars(select(Notification)).all()
            assert len(notifs) == 1
            assert notifs[0].recipient_user_id == claimant_id
            # Audit events recorded
            actions = db.scalars(select(AuditEvent.action).where(AuditEvent.org_id == org_id)).all()
            assert 'organization.claim_requested' in actions
            assert 'organization.claim_approved' in actions
            assert 'organization.member_added' in actions
    finally:
        settings.upload_dir, settings.r2_enabled = old_upload_dir
        engine.dispose()
        shutil.rmtree(tmp_dir, ignore_errors=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_competing_claims_and_existing_owner_approval():
    engine, sessions, app, settings, old_upload_dir, tmp_dir = _setup()
    try:
        with sessions() as db:
            owner = User(username='owner1', phone='+966501000001', phone_e164='+966501000001', phone_verified=True, password_hash='x')
            user_a = User(username='user_a', phone='+966501000002', phone_e164='+966501000002', phone_verified=True, password_hash='x')
            user_b = User(username='user_b', phone='+966501000003', phone_e164='+966501000003', phone_verified=True, password_hash='x')
            unclaimed_org = Organization(name='Unclaimed Pipes Co.', name_normalized='unclaimed pipes co.', source='imported', is_supplier=True)
            owned_org = Organization(name='Owned Cables Co.', name_normalized='owned cables co.', source='imported', is_supplier=True)
            db.add_all([owner, user_a, user_b, unclaimed_org, owned_org])
            db.flush()
            db.add(Member(organization_id=owned_org.id, user_id=owner.id, role='owner'))
            db.commit()
            owner_id, ua_id, ub_id = owner.id, user_a.id, user_b.id
            unclaimed_id, owned_id = unclaimed_org.id, owned_org.id

        with TestClient(app) as client:
            # Two competing claims on an unclaimed org
            app.dependency_overrides[current_user] = lambda: User(id=ua_id, phone_verified=True)
            c_a = client.post(f'/organizations/{unclaimed_id}/claims', json={'evidence': 'Claim A evidence from user A.'}).json()
            app.dependency_overrides[current_user] = lambda: User(id=ub_id, phone_verified=True)
            c_b = client.post(f'/organizations/{unclaimed_id}/claims', json={'evidence': 'Claim B evidence from user B.'}).json()

            # Admin approves Claim A -> Claim B is automatically superseded/rejected; cannot create duplicate owner
            admin = User(id=owner_id, is_admin=True)
            app.dependency_overrides[admin_user] = lambda: admin
            app.dependency_overrides[current_user] = lambda: admin
            assert client.post(f'/organizations/admin/review/claim/{c_a["id"]}', json={'approve': True, 'notes': 'Approved A'}).status_code == 200
            second_try = client.post(f'/organizations/admin/review/claim/{c_b["id"]}', json={'approve': True, 'notes': 'Try B'})
            assert second_try.status_code == 409

            # Competing claim on an already-owned org notifies existing owner and requires owner/admin approval
            app.dependency_overrides[current_user] = lambda: User(id=ub_id, phone_verified=True)
            competing = client.post(f'/organizations/{owned_id}/claims', json={'evidence': 'I work at Owned Cables Co. as sales manager.'})
            assert competing.status_code == 200, competing.text
            assert competing.json()['has_existing_owner'] is True
            comp_id = competing.json()['id']

            # Non-owner cannot list or decide org claims (org_member returns 404 to prevent org enumeration)
            assert client.get(f'/organizations/{owned_id}/claims').status_code in (403, 404)
            assert client.post(f'/organizations/{owned_id}/claims/{comp_id}/decision', json={'approve': True, 'notes': 'Self approve'}).status_code in (403, 404)

            # Existing owner sees competing claim and approves as manager
            app.dependency_overrides[current_user] = lambda: User(id=owner_id, phone_verified=True)
            org_claims = client.get(f'/organizations/{owned_id}/claims')
            assert org_claims.status_code == 200
            assert len(org_claims.json()) == 1
            dec = client.post(
                f'/organizations/{owned_id}/claims/{comp_id}/decision',
                json={'approve': True, 'notes': 'Confirmed employee.', 'assigned_role': 'manager'},
            )
            assert dec.status_code == 200, dec.text
            assert dec.json()['assigned_role'] == 'manager'

        with sessions() as db:
            # Unclaimed org has exactly 1 owner
            unclaimed_owners = db.scalars(select(Member).where(Member.organization_id == unclaimed_id, Member.role == 'owner')).all()
            assert len(unclaimed_owners) == 1
            assert unclaimed_owners[0].user_id == ua_id
            # Owned org owner received competing claim notification
            owner_notifs = db.scalars(select(Notification).where(Notification.recipient_user_id == owner_id, Notification.event == 'organization.competing_claim_received')).all()
            assert len(owner_notifs) == 1
    finally:
        settings.upload_dir, settings.r2_enabled = old_upload_dir
        engine.dispose()
        shutil.rmtree(tmp_dir, ignore_errors=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_unverified_phone_cannot_claim_and_buyer_org_not_claimable():
    engine, sessions, app, settings, old_upload_dir, tmp_dir = _setup()
    try:
        with sessions() as db:
            u = User(username='unverified', phone='+966502223344', phone_e164='+966502223344', phone_verified=False, password_hash='x')
            buyer_org = Organization(name='Buyer Only Co.', name_normalized='buyer only co.', is_buyer=True, is_supplier=False)
            supplier_org = Organization(name='Supplier Co.', name_normalized='supplier co.', is_buyer=False, is_supplier=True, source='imported')
            db.add_all([u, buyer_org, supplier_org])
            db.commit()
            uid, buyer_id, sup_id = u.id, buyer_org.id, supplier_org.id

        with TestClient(app) as client:
            app.dependency_overrides[current_user] = lambda: User(id=uid, phone_verified=False)
            assert client.post(f'/organizations/{sup_id}/claims', json={'evidence': 'Attempting claim without OTP verification.'}).status_code == 403
            app.dependency_overrides[current_user] = lambda: User(id=uid, phone_verified=True)
            assert client.post(f'/organizations/{buyer_id}/claims', json={'evidence': 'Attempting to claim a buyer-only org.'}).status_code == 404
    finally:
        settings.upload_dir, settings.r2_enabled = old_upload_dir
        engine.dispose()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_duplicate_supplier_cr_blocked_and_claim_rejection_flow():
    engine, sessions, app, settings, old_upload_dir, tmp_dir = _setup()
    try:
        with sessions() as db:
            user = User(username='dup_user', phone='+966503334455', phone_e164='+966503334455', phone_verified=True, password_hash='x')
            admin = User(username='admin_rej', phone='+966509998877', phone_e164='+966509998877', phone_verified=True, is_admin=True, password_hash='x')
            imported = Organization(
                name='Imported Pipes Ltd.',
                name_normalized='imported pipes ltd.',
                cr_number='1010888999',
                business_email='private@pipes.sa',
                business_phone='+966501234567',
                source='imported',
                is_supplier=True,
                is_buyer=False,
                verified_status='unverified',
            )
            db.add_all([user, admin, imported])
            db.commit()
            uid, admin_id, org_id = user.id, admin.id, imported.id

        with TestClient(app) as client:
            app.dependency_overrides[current_user] = lambda: User(id=uid, phone_verified=True)

            # Safe public fields only from /claimable/suppliers as well
            pub_res = client.get('/organizations/claimable/suppliers')
            assert pub_res.status_code == 200
            assert len(pub_res.json()) == 1
            assert 'business_email' not in pub_res.json()[0]
            assert 'business_phone' not in pub_res.json()[0]
            assert 'cr_number' not in pub_res.json()[0]

            # Creating a new supplier with an existing supplier CR is blocked
            dup_org = client.post('/organizations', json={
                'name': 'Imported Pipes Duplicate',
                'cr_number': '1010-888-999',
                'city': 'Riyadh',
                'is_buyer': False,
                'is_supplier': True,
            })
            assert dup_org.status_code == 409

            # Submit claim then admin rejects it
            claim_res = client.post(f'/organizations/{org_id}/claims', json={'evidence': 'Requesting access with incomplete proof.'})
            assert claim_res.status_code == 200
            claim_id = claim_res.json()['id']

            app.dependency_overrides[admin_user] = lambda: User(id=admin_id, is_admin=True)
            app.dependency_overrides[current_user] = lambda: User(id=admin_id, is_admin=True)
            rej_res = client.post(
                f'/organizations/admin/review/claim/{claim_id}',
                json={'approve': False, 'notes': 'Please attach CR certificate or authorization letter.'},
            )
            assert rej_res.status_code == 200
            assert rej_res.json()['status'] == 'rejected'

            admin_list = client.get('/organizations/admin/claims', params={'status': 'rejected'})
            assert admin_list.status_code == 200
            assert len(admin_list.json()) == 1
            assert admin_list.json()[0]['id'] == claim_id

            # Claimant sees rejected status and still has no org membership
            app.dependency_overrides[current_user] = lambda: User(id=uid, phone_verified=True)
            mine = client.get('/organizations/claims/mine')
            assert mine.status_code == 200
            assert mine.json()[0]['status'] == 'rejected'
            assert 'CR certificate' in mine.json()[0]['reviewer_notes']
            assert client.get('/organizations').json() == []
    finally:
        settings.upload_dir, settings.r2_enabled = old_upload_dir
        engine.dispose()
        shutil.rmtree(tmp_dir, ignore_errors=True)
