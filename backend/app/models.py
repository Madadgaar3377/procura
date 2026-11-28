from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    column,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.identity_columns import email_identity


def uid(): return str(uuid4())
def utcnow(): return datetime.now(UTC)

class TimeID:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class User(TimeID, Base):
    __tablename__='users'
    __table_args__=(CheckConstraint(r"phone_e164 ~ '^\+[1-9][0-9]{1,14}$'", name='ck_users_phone_e164_format').ddl_if(dialect='postgresql'),
    Index('uq_users_email_identity', email_identity(column('email')), unique=True),
    Index(
        'uq_users_email_lower',
        text('lower(email)'),
        unique=True,
        sqlite_where=text('email IS NOT NULL'),
        postgresql_where=text('email IS NOT NULL'),
    ),)
    notification_language: Mapped[str] = mapped_column(String(2), default='en', server_default='en')
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    phone_e164: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    phone_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    sms_notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    email_notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    password_hash: Mapped[str] = mapped_column(String(255))
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    avatar_file_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approval_status: Mapped[str] = mapped_column(String(30), default='pending', server_default='approved', index=True)
    pending_approval_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    approval_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    manual_review_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    manual_review_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    preferred_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_workspace_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_org_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))

class AccountPreference(Base):
    __tablename__ = 'account_preferences'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    events: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text('true'))

class RefreshSession(TimeID, Base):
    __tablename__='refresh_sessions'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    family_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)

class PhoneOTP(TimeID, Base):
    __tablename__='phone_otps'
    phone: Mapped[str] = mapped_column(String(30), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True)
    previous_phone: Mapped[str | None] = mapped_column(String(16), nullable=True)
    credential_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    purpose: Mapped[str] = mapped_column(String(24))
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)

class EmailOTP(TimeID, Base):
    __tablename__='email_otps'
    __table_args__=(Index('ix_email_otps_reset_link_hash','code_hash',
        postgresql_where=text("purpose = 'password_reset_link'"),
        sqlite_where=text("purpose = 'password_reset_link'")),)
    # Also stores durable password-reset requests; ordinary verification rows
    # leave these fields NULL. Never authorize a reset from an unbound row.
    user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True)
    credential_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email: Mapped[str] = mapped_column(String(200), index=True)
    purpose: Mapped[str] = mapped_column(String(24))
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)

class Organization(TimeID, Base):
    __tablename__='organizations'
    __table_args__=(Index(
        'uq_organization_verified_cr',
        'cr_number',
        unique=True,
        sqlite_where=text("verified_status = 'verified' AND cr_number IS NOT NULL AND (is_active = true OR is_supplier = false)"),
        postgresql_where=text("verified_status = 'verified' AND cr_number IS NOT NULL AND (is_active = true OR is_supplier = false)"),
    ),Index('ix_organizations_supplier_active_verified','is_supplier','is_active','verified_status'),)
    name: Mapped[str] = mapped_column(String(220), index=True)
    name_normalized: Mapped[str] = mapped_column(String(220), index=True)
    cr_number: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    vat_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    website: Mapped[str | None] = mapped_column(String(300), nullable=True)
    trading_name: Mapped[str | None] = mapped_column(String(220), nullable=True)
    company_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    designation: Mapped[str | None] = mapped_column(String(100), nullable=True)
    business_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    business_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(String(30), default='registered')
    verified_status: Mapped[str] = mapped_column(String(30), default='unverified', index=True)
    profile_status: Mapped[str] = mapped_column(String(30), default='incomplete', server_default='incomplete', index=True)
    is_buyer: Mapped[bool] = mapped_column(Boolean, default=True)
    is_supplier: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    logo_file_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    quote_automation: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
    pending_approval_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    manual_review_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    manual_review_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)

class SupplierProfileDetails(TimeID, Base):
    """Admin-curated supplier profile facts kept apart from the free-text description.

    ``imported_scope_text`` preserves product/category wording that an import placed in
    ``organizations.description``; the description repair moves it here instead of deleting it.
    """
    __tablename__ = 'supplier_profile_details'
    org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), unique=True, index=True)
    business_types: Mapped[list] = mapped_column(JSON, default=list, server_default='[]')
    business_services: Mapped[list] = mapped_column(JSON, default=list, server_default='[]')
    imported_scope_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text('CURRENT_TIMESTAMP'))


class OrganizationLocation(TimeID, Base):
    """A physical company site. Delivery coverage stays on supplier_capabilities.city."""
    __tablename__ = 'organization_locations'
    __table_args__ = (
        CheckConstraint("kind IN ('head_office','office','branch','factory','warehouse','showroom')", name='ck_organization_location_kind'),
        Index('ix_organization_locations_org_kind', 'org_id', 'kind'),
    )
    org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(300), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default='0')


class PublicProfileConsent(Base):
    __tablename__ = 'organization_public_consent'
    org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), primary_key=True)
    fields: Mapped[list[str]] = mapped_column(JSON, default=list)


class Member(TimeID, Base):
    __tablename__='organization_members'
    __table_args__=(UniqueConstraint('user_id','organization_id',name='uq_user_org'),)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'))
    role: Mapped[str] = mapped_column(String(30), default='viewer')

class FavoriteSupplier(TimeID, Base):
    __tablename__='favorite_suppliers'
    __table_args__=(UniqueConstraint('buyer_org_id','supplier_org_id',name='uq_favorite_supplier'),)
    buyer_org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'), index=True)
    supplier_org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'), index=True)

class Contact(TimeID, Base):
    __tablename__='organization_contacts'
    org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'), index=True)
    name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    email: Mapped[str | None] = mapped_column(String(220), nullable=True)
    phone_normalized: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    fax: Mapped[str | None] = mapped_column(String(80), nullable=True)
    raw_phone: Mapped[str | None] = mapped_column(String(300), nullable=True)
    raw_email: Mapped[str | None] = mapped_column(String(500), nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    permission_to_notify: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    merged_into_contact_id: Mapped[str | None] = mapped_column(ForeignKey('organization_contacts.id'), nullable=True, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class ContactChannel(TimeID, Base):
    """A normalized email, phone, or fax attached to one organization contact."""
    __tablename__ = 'organization_contact_channels'
    __table_args__ = (
        UniqueConstraint('contact_id', 'kind', 'normalized_value', name='uq_contact_channel_identity'),
        Index('ix_contact_channel_org_kind_normalized', 'org_id', 'kind', 'normalized_value'),
    )
    contact_id: Mapped[str] = mapped_column(ForeignKey('organization_contacts.id', ondelete='CASCADE'), index=True)
    org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), index=True)
    kind: Mapped[str] = mapped_column(String(12))
    value: Mapped[str] = mapped_column(String(300))
    normalized_value: Mapped[str] = mapped_column(String(220))
    label: Mapped[str] = mapped_column(String(20), default='other', server_default='other')
    extension: Mapped[str | None] = mapped_column(String(12), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    source_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)


class ContactImportSource(TimeID, Base):
    __tablename__ = 'organization_contact_import_sources'
    __table_args__ = (UniqueConstraint('contact_id', 'source_reference', name='uq_contact_import_source'),)
    contact_id: Mapped[str] = mapped_column(ForeignKey('organization_contacts.id', ondelete='CASCADE'), index=True)
    org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), index=True)
    source_reference: Mapped[str] = mapped_column(String(120))
    raw_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    raw_email: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    raw_phone: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class ContactCapability(TimeID, Base):
    __tablename__ = 'supplier_contact_capabilities'
    __table_args__ = (
        UniqueConstraint('contact_id','capability_id',name='uq_supplier_contact_capability'),
        Index('ix_supplier_contact_capabilities_capability_contact','capability_id','contact_id'),
    )
    contact_id: Mapped[str] = mapped_column(ForeignKey('organization_contacts.id',ondelete='CASCADE'),index=True)
    capability_id: Mapped[str] = mapped_column(ForeignKey('supplier_capabilities.id',ondelete='CASCADE'),index=True)
    material_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean,default=False,server_default=text('false'))

class ContactInquiry(TimeID, Base):
    __tablename__='contact_inquiries'
    ticket_reference: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    submission_key: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), index=True, nullable=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey('organizations.id', ondelete='SET NULL'), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(200), index=True)
    company: Mapped[str | None] = mapped_column(String(220), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    topic: Mapped[str] = mapped_column(String(40))
    user_type: Mapped[str] = mapped_column(String(20), default='other', server_default='other')
    category: Mapped[str] = mapped_column(String(40), default='general_inquiry', server_default='general_inquiry', index=True)
    subject: Mapped[str] = mapped_column(String(200), default='General inquiry', server_default='General inquiry')
    message: Mapped[str] = mapped_column(Text)
    internal_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='new', server_default='new', index=True)
    handled_by: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text('CURRENT_TIMESTAMP'))

class CompanyClaim(TimeID, Base):
    __tablename__='organization_claims'
    __table_args__=(
        Index('ix_org_claims_org_status','org_id','status'),
        Index('ix_org_claims_claimant_status','claimant_id','status'),
        Index('uq_org_claims_pending_user_org','org_id','claimant_id',unique=True,
              postgresql_where=text("status = 'pending'"),
              sqlite_where=text("status = 'pending'")),
    )
    org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'))
    claimant_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    evidence: Mapped[str] = mapped_column(Text)
    document_key: Mapped[str | None] = mapped_column(String(400), nullable=True)
    matched_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    requested_cr_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    assigned_role: Mapped[str] = mapped_column(String(30), default='owner', server_default='owner')
    has_existing_owner: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    status: Mapped[str] = mapped_column(String(30), default='pending')
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class Verification(TimeID, Base):
    __tablename__='verifications'
    org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'))
    cr_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    document_key: Mapped[str | None] = mapped_column(String(400), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='pending')
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class Category(TimeID, Base):
    __tablename__='categories'
    __table_args__=(UniqueConstraint('code',name='uq_category_code'),Index('ix_categories_parent_name_normalized','parent_id','normalized_name'))
    code: Mapped[str] = mapped_column(String(60), index=True)
    name_en: Mapped[str] = mapped_column(String(180))
    normalized_name: Mapped[str | None] = mapped_column(String(180), default='', nullable=True, index=True)
    name_ar: Mapped[str] = mapped_column(String(180), default='')
    parent_id: Mapped[str | None] = mapped_column(ForeignKey('categories.id'), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text('CURRENT_TIMESTAMP'))

class CategoryReviewDecision(TimeID, Base):
    """Explicit admin review decisions and audit history for material categories."""
    __tablename__='category_review_decisions'
    __table_args__=(
        Index('ix_cat_review_category_created','category_id','created_at'),
        Index('ix_cat_review_category_current','category_id','is_current'),
    )
    category_id: Mapped[str] = mapped_column(ForeignKey('categories.id', ondelete='CASCADE'), index=True)
    decision: Mapped[str] = mapped_column(String(30))
    issue_type: Mapped[str] = mapped_column(String(40))
    discipline: Mapped[str | None] = mapped_column(String(30), nullable=True)
    merged_into_category_id: Mapped[str | None] = mapped_column(ForeignKey('categories.id', ondelete='SET NULL'), nullable=True)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text('true'))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text('CURRENT_TIMESTAMP'))

class MeasurementUnit(TimeID, Base):
    __tablename__='measurement_units'
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name_en: Mapped[str] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

class Material(TimeID, Base):
    __tablename__='materials'
    __table_args__=(Index('ix_materials_category_sort','category_id','code'),Index('ix_materials_subcategory_sort','subcategory_id','code'),Index('ix_materials_active_unit','active','unit'),Index('ix_materials_active_cats','active','main_category_id','subcategory_id'))
    code: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    category_id: Mapped[str] = mapped_column(ForeignKey('categories.id'), index=True)
    main_category_id: Mapped[str | None] = mapped_column(ForeignKey('categories.id'), nullable=True, index=True)
    subcategory_id: Mapped[str | None] = mapped_column(ForeignKey('categories.id'), nullable=True, index=True)
    name_en: Mapped[str] = mapped_column(String(250), index=True)
    normalized_description: Mapped[str | None] = mapped_column(String(250), default='', nullable=True, index=True)
    name_ar: Mapped[str] = mapped_column(String(250), default='')
    arabic_description: Mapped[str] = mapped_column(String(250), default='')
    description: Mapped[str] = mapped_column(Text, default='')
    unit: Mapped[str] = mapped_column(String(30), default='No.')
    source_unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    specs_template: Mapped[dict] = mapped_column(JSON, default=dict)
    discipline: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    source_sn: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    source_material_code: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    expenditure_code: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    source_identity: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    import_batch_id: Mapped[str | None] = mapped_column(ForeignKey('import_batches.id'), nullable=True, index=True)
    import_row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text('CURRENT_TIMESTAMP'))

class MaterialRevision(TimeID, Base):
    __tablename__='material_revisions'
    __table_args__=(UniqueConstraint('material_id','version',name='uq_material_revision'),Index('ix_material_revisions_material_created','material_id','created_at'))
    material_id: Mapped[str] = mapped_column(ForeignKey('materials.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    action: Mapped[str] = mapped_column(String(30))
    snapshot: Mapped[dict] = mapped_column(JSON)

class ImportBatch(TimeID, Base):
    __tablename__='import_batches'
    __table_args__ = (
        Index('uq_supplier_bulk_file_admin', 'initiated_by', 'source_sha256', unique=True,
              postgresql_where=text("kind = 'supplier_bulk' AND status IN ('queued','processing')"), sqlite_where=text("kind = 'supplier_bulk' AND status IN ('queued','processing')")),
        Index('ix_import_job_queue', 'kind', 'status', 'created_at'),
        )
    kind: Mapped[str] = mapped_column(String(30))
    filename: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default='processing')
    rows: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0)
    categories_created: Mapped[int] = mapped_column(Integer, default=0)
    completed_rows: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(30), default='preview')
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    source_storage_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source_bytes: Mapped[int] = mapped_column(Integer, default=0)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    import_options: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    preview_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    upload_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    parse_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    apply_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    initiated_by: Mapped[str] = mapped_column(ForeignKey('users.id'))

class StoredFile(TimeID, Base):
    __tablename__ = 'stored_files'
    __table_args__ = (UniqueConstraint('object_key', name='uq_stored_files_object_key'),
                      Index('ix_stored_files_status_created', 'status', 'created_at'),
                      Index('ix_stored_files_purpose_entity', 'purpose', 'entity_id'))
    bucket_type: Mapped[str] = mapped_column(String(12))
    object_key: Mapped[str] = mapped_column(String(500))
    original_filename: Mapped[str] = mapped_column(String(200))
    safe_filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(120))
    file_extension: Mapped[str] = mapped_column(String(12))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(200), nullable=True)
    visibility: Mapped[str] = mapped_column(String(12), default='private')
    purpose: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(24), default='pending_upload', index=True)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey('organizations.id'), nullable=True, index=True)
    uploaded_by_user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(24))
    entity_id: Mapped[str] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class ImportedRow(TimeID, Base):
    __tablename__='import_rows'
    __table_args__ = (Index('ix_import_row_job_status', 'batch_id', 'status', 'created_at'), Index('ix_import_rows_batch_status_row', 'batch_id', 'status', 'row_number'))
    batch_id: Mapped[str] = mapped_column(ForeignKey('import_batches.id'), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    raw: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30))
    issue: Mapped[str | None] = mapped_column(String(300), nullable=True)

class ImportedSupplierTrade(TimeID, Base):
    __tablename__='imported_supplier_trades'
    org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'), index=True)
    import_batch_id: Mapped[str] = mapped_column(ForeignKey('import_batches.id'))
    raw_trade: Mapped[str] = mapped_column(Text, default='')
    raw_products: Mapped[str] = mapped_column(Text, default='', server_default='')
    raw_brands: Mapped[str] = mapped_column(Text, default='')
    raw_type: Mapped[str] = mapped_column(String(150), default='')
    raw_location: Mapped[str] = mapped_column(String(150), default='')
    mapping_status: Mapped[str] = mapped_column(String(30), default='unreviewed')

class ContactSupplierTrade(Base):
    """Exact source responsibility, retained even before taxonomy approval."""
    __tablename__ = 'contact_supplier_trades'
    contact_id: Mapped[str] = mapped_column(ForeignKey('organization_contacts.id'), primary_key=True)
    trade_id: Mapped[str] = mapped_column(ForeignKey('imported_supplier_trades.id'), primary_key=True, index=True)


class Capability(TimeID, Base):
    __tablename__='supplier_capabilities'
    __table_args__=(
        CheckConstraint("eligibility_status IN ('enabled','suspended','revoked','rejected')",name='ck_capability_eligibility'),
        Index('ix_capability_eligible_scope','category_id','city','eligibility_status','confirmed','accepts_rfqs'),
        UniqueConstraint('org_id','category_id','city',name='uq_capability_area'),
        Index('ix_capability_match_area','category_id','city','confirmed','accepts_rfqs'),
        Index('ix_capability_material_links', text('(material_ids::jsonb)'),
              postgresql_using='gin').ddl_if(dialect='postgresql'),
    )
    org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'), index=True)
    category_id: Mapped[str] = mapped_column(ForeignKey('categories.id'), index=True)
    city: Mapped[str] = mapped_column(String(100), index=True)
    qualifications: Mapped[list] = mapped_column(JSON, default=list)
    material_ids: Mapped[list] = mapped_column(JSON, default=list)
    brands: Mapped[list] = mapped_column(JSON, default=list, server_default='[]')
    trade_keywords: Mapped[str] = mapped_column(Text, default='')
    mapping_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    accepts_rfqs: Mapped[bool] = mapped_column(Boolean, default=True)
    eligibility_status: Mapped[str] = mapped_column(String(20), default='enabled', server_default='enabled')
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

class PriceBookItem(TimeID, Base):
    __tablename__='price_book_items'
    __table_args__=(CheckConstraint('unit_price > 0',name='ck_private_rate_positive'),CheckConstraint('min_order_quantity IS NULL OR min_order_quantity > 0',name='ck_private_rate_moq_positive'),Index('uq_private_rate_active','org_id','material_id','price_basis',unique=True,sqlite_where=text('active = 1'),postgresql_where=text('active = true')),Index('ix_private_rate_org_active_material','org_id','active','material_id','price_basis'),Index('ix_private_rate_org_material','org_id','material_id','price_basis'),Index('ix_private_rate_org_status_updated','org_id','active','updated_at'),Index('ix_private_rate_org_created','org_id','created_at','id'),Index('ix_private_rate_org_categories','org_id','main_category_id','subcategory_id'),Index('uq_private_custom_rate_active','org_id','custom_key','price_basis',unique=True,sqlite_where=text('active = 1 AND material_id IS NULL'),postgresql_where=text('active = true AND material_id IS NULL')),CheckConstraint("material_id IS NOT NULL OR (main_category_id IS NOT NULL AND length(trim(supplier_description)) >= 3 AND length(trim(unit)) > 0 AND custom_key IS NOT NULL)",name='ck_private_custom_rate_details'))
    org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'), index=True)
    material_id: Mapped[str | None] = mapped_column(ForeignKey('materials.id'), nullable=True, index=True)
    main_category_id: Mapped[str | None] = mapped_column(ForeignKey('categories.id'), nullable=True)
    subcategory_id: Mapped[str | None] = mapped_column(ForeignKey('categories.id'), nullable=True)
    supplier_description: Mapped[str] = mapped_column(Text, default='', server_default='')
    custom_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    brand: Mapped[str] = mapped_column(String(150), default='')
    model: Mapped[str] = mapped_column(String(150), default='')
    offered_specs: Mapped[str] = mapped_column(Text, default='')
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14,2))
    min_order_quantity: Mapped[Decimal | None] = mapped_column(Numeric(14,3), nullable=True)
    vat_rate: Mapped[Decimal | None] = mapped_column(Numeric(5,4), nullable=True)
    lead_days: Mapped[int] = mapped_column(Integer, default=7)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    unit: Mapped[str] = mapped_column(String(30), default='')
    currency: Mapped[str] = mapped_column(String(5), default='SAR', server_default='SAR')
    availability: Mapped[str] = mapped_column(String(20), default='unknown', server_default='unknown')
    price_basis: Mapped[str] = mapped_column(String(30), default='supply', server_default='supply')
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text('true'))
    offered_description: Mapped[str] = mapped_column(Text, default='', server_default='')
    remarks: Mapped[str] = mapped_column(Text, default='', server_default='')
    technical_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    material_version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    warranty_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payment_terms: Mapped[str] = mapped_column(String(300), default='', server_default='')
    stock_quantity: Mapped[Decimal | None] = mapped_column(Numeric(14,3), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text('CURRENT_TIMESTAMP'))

class RFQ(TimeID, Base):
    __tablename__='rfqs'
    __table_args__=(Index('uq_rfq_buyer_request','buyer_org_id','client_request_id',unique=True,
        sqlite_where=text('client_request_id IS NOT NULL'),postgresql_where=text('client_request_id IS NOT NULL')),)
    public_display_authorized: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    reference: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    buyer_org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'), index=True)
    client_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(String(250))
    city: Mapped[str] = mapped_column(String(120))
    response_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default='draft', index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    required_qualifications: Mapped[list[str]] = mapped_column(JSON, default=list)
    project_name: Mapped[str] = mapped_column(String(250), default='', server_default='')
    required_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    additional_instructions: Mapped[str] = mapped_column(Text, default='', server_default='')
    attachment_records: Mapped[list[dict]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    # Buyer-configurable quotation ranking weights; NULL means the client defaults 50/30/20.
    scoring_weights: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    delivery_address: Mapped[str] = mapped_column(String(300), default='', server_default='')

class RFQItem(TimeID, Base):
    __tablename__='rfq_items'
    rfq_id: Mapped[str] = mapped_column(ForeignKey('rfqs.id'), index=True)
    material_id: Mapped[str | None] = mapped_column(ForeignKey('materials.id'), nullable=True, index=True)
    main_category_id: Mapped[str | None] = mapped_column(ForeignKey('categories.id'), nullable=True, index=True)
    subcategory_id: Mapped[str | None] = mapped_column(ForeignKey('categories.id'), nullable=True, index=True)
    description: Mapped[str] = mapped_column(Text)
    specifications: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(30))
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3))

class RFQMatch(TimeID, Base):
    __tablename__='rfq_matches'
    __table_args__=(UniqueConstraint('rfq_id','supplier_org_id',name='uq_match'),Index('ix_rfq_matches_rfq_status','rfq_id','status'),)
    rfq_id: Mapped[str] = mapped_column(ForeignKey('rfqs.id'), index=True)
    supplier_org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'))
    score: Mapped[int] = mapped_column(Integer)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default='eligible')

class Invitation(TimeID, Base):
    __tablename__='rfq_invitations'
    __table_args__=(UniqueConstraint('rfq_id','supplier_org_id',name='uq_invitation'),)
    rfq_id: Mapped[str] = mapped_column(ForeignKey('rfqs.id'), index=True)
    supplier_org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'), index=True)
    status: Mapped[str] = mapped_column(String(30), default='invited')
    invited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # RFQ item ids this supplier matched; the portal's default "Matched Items" view.
    matched_item_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Superseded before delivery because the supplier no longer qualifies (record kept).
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Manual buyer reminders: per-supplier cooldown and history count.
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    # Persisted per-line match evidence ({item_id: {material_ids, category_ids}}) written by
    # the matching job, so recipient routing never re-runs supplier matching.
    match_evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)

class Quotation(TimeID, Base):
    __tablename__='quotations'
    __table_args__=(UniqueConstraint('invitation_id',name='uq_quote_invitation'),Index('ix_quotations_supplier_status','supplier_org_id','status'))
    invitation_id: Mapped[str] = mapped_column(ForeignKey('rfq_invitations.id'))
    rfq_id: Mapped[str] = mapped_column(ForeignKey('rfqs.id'), index=True)
    supplier_org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'))
    status: Mapped[str] = mapped_column(String(30), default='draft')
    price_basis: Mapped[str] = mapped_column(String(30), default='supply')
    currency: Mapped[str] = mapped_column(String(5), default='SAR')
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5,4), default=Decimal('0.15'))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)

    submission_source: Mapped[str] = mapped_column(String(30), default='manual', server_default='manual')
    review_required: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text('true'))
    review_reasons: Mapped[list] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    policy_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    commercial_terms: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))

class QuoteLine(TimeID, Base):
    __tablename__='quotation_lines'
    __table_args__=(UniqueConstraint('quotation_id','rfq_item_id',name='uq_quote_item'),)
    quotation_id: Mapped[str] = mapped_column(ForeignKey('quotations.id'), index=True)
    rfq_item_id: Mapped[str] = mapped_column(ForeignKey('rfq_items.id'))
    brand: Mapped[str] = mapped_column(String(150), default='')
    model: Mapped[str] = mapped_column(String(150), default='')
    offered_specs: Mapped[str] = mapped_column(Text, default='')
    compliance: Mapped[str] = mapped_column(String(30), default='unconfirmed')
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14,2))
    lead_days: Mapped[int] = mapped_column(Integer, default=7)
    note: Mapped[str] = mapped_column(Text, default='')

    offered_description: Mapped[str] = mapped_column(Text, default='', server_default='')
    source_rate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_rate_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_rate_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    vat_rate: Mapped[Decimal | None] = mapped_column(Numeric(5,4), nullable=True)
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5,2), default=Decimal('0'), server_default='0')
    warranty_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    availability: Mapped[str] = mapped_column(String(20), default='unknown', server_default='unknown')

class Notification(TimeID, Base):
    __tablename__='notifications'
    recipient_user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    event: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(String(500))
    channel: Mapped[str] = mapped_column(String(30), default='in_app')
    status: Mapped[str] = mapped_column(String(30), default='pending')
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_outbox_id: Mapped[str | None] = mapped_column(ForeignKey('outbox_events.id'), nullable=True, index=True)

class DeliveryRestriction(TimeID, Base):
    """Normalized destination restrictions shared across all supplier identities."""
    __tablename__ = 'notification_delivery_restrictions'
    __table_args__ = (UniqueConstraint('channel', 'destination', name='uq_delivery_restriction'),)
    channel: Mapped[str] = mapped_column(String(10))
    destination: Mapped[str] = mapped_column(String(220))
    reason: Mapped[str] = mapped_column(String(30))
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Bounce bookkeeping. A cleared restriction (admin corrected/revalidated the address)
    # is kept for history and no longer blocks delivery.
    bounce_count: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cleared_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class EmailDeliveryEvent(TimeID, Base):
    """Provider delivery feedback (DSN bounce, webhook delivery/bounce, admin evidence).

    ``external_id`` is the provider event id or a digest of the bounce report, unique
    per source, so the same report processed twice is applied once.
    """
    __tablename__ = 'email_delivery_events'
    __table_args__ = (
        UniqueConstraint('source', 'external_id', name='uq_email_delivery_event_source'),
        Index('ix_email_delivery_events_outbox', 'outbox_id'),
        Index('ix_email_delivery_events_destination', 'destination_hash'),
    )
    source: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(128))
    outbox_id: Mapped[str | None] = mapped_column(ForeignKey('outbox_events.id', ondelete='SET NULL'), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    event_type: Mapped[str] = mapped_column(String(30))
    destination_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    diagnostic: Mapped[str | None] = mapped_column(String(500), nullable=True)
    applied: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Outbox(TimeID, Base):
    __tablename__='outbox_events'
    __table_args__=(
        Index('uq_outbox_dedupe_key','dedupe_key',unique=True),
        Index('ix_outbox_status_next_attempt','status','next_attempt_at'),
        Index('ix_outbox_event_submitted_at','event','submitted_at'),
    )
    event: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default='pending')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    dedupe_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lock_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    delivery_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status_checks: Mapped[int] = mapped_column(Integer, default=0, server_default=text('0'))
    secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When the message was handed to the provider (provider sending-limit accounting).
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class MembershipPlan(TimeID, Base):
    __tablename__='membership_plans'
    __table_args__=(CheckConstraint('monthly_price >= 0 AND yearly_price >= 0',name='ck_membership_prices'),
        CheckConstraint("publication_status IN ('draft','published','unpublished')",name='ck_membership_publication'),
        CheckConstraint('available_until IS NULL OR available_from IS NULL OR available_until > available_from',name='ck_membership_dates'))
    name: Mapped[str] = mapped_column(String(80), unique=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, default=lambda:'plan-'+uid())
    publication_status: Mapped[str] = mapped_column(String(20), default='draft', server_default='draft')
    visible_on_website: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text('true'))
    available_for_subscription: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text('true'))
    billing_intervals: Mapped[list[str]] = mapped_column(JSON, default=lambda:['monthly','yearly'], server_default='["monthly","yearly"]')
    feature_details: Mapped[list[dict]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    available_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, server_default=text('CURRENT_TIMESTAMP'))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    description: Mapped[str] = mapped_column(Text, default='', server_default='')
    monthly_price: Mapped[Decimal] = mapped_column(Numeric(12,2), default=0, server_default='0')
    yearly_price: Mapped[Decimal] = mapped_column(Numeric(12,2), default=0, server_default='0')
    currency: Mapped[str] = mapped_column(String(3), default='SAR', server_default='SAR')
    features: Mapped[list[str]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text('true'))
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    recommended: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    premium: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    display_order: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    quote_limit: Mapped[int] = mapped_column(Integer, default=10)
    auto_drafts: Mapped[bool] = mapped_column(Boolean, default=False)
    team_limit: Mapped[int] = mapped_column(Integer, default=1)

class Subscription(TimeID, Base):
    __tablename__='subscriptions'
    org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'), index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey('membership_plans.id'))
    status: Mapped[str] = mapped_column(String(30), default='active')
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    price_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(12,2), nullable=True)
    currency_snapshot: Mapped[str | None] = mapped_column(String(3), nullable=True)
    billing_interval: Mapped[str] = mapped_column(String(10), default='monthly', server_default='monthly')
    premium_snapshot: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    plan_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

class MembershipSettings(Base):
    __tablename__='membership_settings'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class PlatformSettings(Base):
    __tablename__='platform_settings'
    id: Mapped[str] = mapped_column(String(20), primary_key=True, default='default')
    auto_approval_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text('true'))
    auto_approval_hours: Mapped[int] = mapped_column(Integer, default=24, server_default='24')
    # Reminder/escalation intervals in hours; see app.modules.procurement.sla.DEFAULT_SLA.
    sla_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text('CURRENT_TIMESTAMP'))

class Promotion(TimeID, Base):
    __tablename__='promotions'
    kind: Mapped[str] = mapped_column(String(30), index=True)
    org_id: Mapped[str | None] = mapped_column(ForeignKey('organizations.id'), nullable=True, index=True)
    rfq_id: Mapped[str | None] = mapped_column(ForeignKey('rfqs.id'), nullable=True, index=True)
    placement: Mapped[str] = mapped_column(String(30), default='homepage')
    priority: Mapped[int] = mapped_column(Integer, default=0)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default='')

class SupplierAccessToken(TimeID, Base):
    """Passwordless magic link scoped to one supplier organization and one RFQ invitation.

    Only a SHA-256 hash of the random token is stored. Tokens expire, can be
    revoked, and are re-authorized against the invitation on every use.
    """
    __tablename__ = 'supplier_access_tokens'
    __table_args__ = (
        Index('ix_supplier_access_tokens_invitation', 'invitation_id', 'revoked_at'),
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    invitation_id: Mapped[str] = mapped_column(ForeignKey('rfq_invitations.id', ondelete='CASCADE'))
    rfq_id: Mapped[str] = mapped_column(ForeignKey('rfqs.id', ondelete='CASCADE'), index=True)
    supplier_org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), index=True)
    channel: Mapped[str] = mapped_column(String(12))
    destination_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0, server_default='0')


class PurchaseOrder(TimeID, Base):
    __tablename__ = 'purchase_orders'
    __table_args__ = (
        UniqueConstraint('quotation_id', name='uq_purchase_order_quotation'),
        Index('ix_purchase_orders_buyer_status', 'buyer_org_id', 'status', 'created_at'),
        Index('ix_purchase_orders_supplier_status', 'supplier_org_id', 'status', 'created_at'),
    )
    reference: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    rfq_id: Mapped[str] = mapped_column(ForeignKey('rfqs.id'), index=True)
    quotation_id: Mapped[str] = mapped_column(ForeignKey('quotations.id'))
    buyer_org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'))
    supplier_org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'))
    # draft, issued, acknowledged, partially_delivered, delivered, cancelled
    status: Mapped[str] = mapped_column(String(30), default='draft')
    currency: Mapped[str] = mapped_column(String(5), default='SAR')
    subtotal: Mapped[Decimal] = mapped_column(Numeric(16,2), default=Decimal('0'))
    discount_total: Mapped[Decimal] = mapped_column(Numeric(16,2), default=Decimal('0'))
    vat_total: Mapped[Decimal] = mapped_column(Numeric(16,2), default=Decimal('0'))
    total: Mapped[Decimal] = mapped_column(Numeric(16,2), default=Decimal('0'))
    delivery_address: Mapped[str] = mapped_column(String(300), default='')
    site_contact: Mapped[str] = mapped_column(String(200), default='')
    payment_terms: Mapped[str] = mapped_column(Text, default='')
    delivery_terms: Mapped[str] = mapped_column(Text, default='')
    special_instructions: Mapped[str] = mapped_column(Text, default='')
    notes: Mapped[str] = mapped_column(Text, default='')
    quotation_reference: Mapped[str] = mapped_column(String(100), default='')
    required_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    prepared_by_user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    issued_by_user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text('CURRENT_TIMESTAMP'))


class PurchaseOrderLine(TimeID, Base):
    __tablename__ = 'purchase_order_lines'
    __table_args__ = (UniqueConstraint('purchase_order_id', 'line_number', name='uq_po_line_number'),)
    purchase_order_id: Mapped[str] = mapped_column(ForeignKey('purchase_orders.id', ondelete='CASCADE'), index=True)
    line_number: Mapped[int] = mapped_column(Integer)
    rfq_item_id: Mapped[str | None] = mapped_column(ForeignKey('rfq_items.id'), nullable=True)
    quote_line_id: Mapped[str | None] = mapped_column(ForeignKey('quotation_lines.id'), nullable=True)
    material_id: Mapped[str | None] = mapped_column(ForeignKey('materials.id'), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    brand: Mapped[str] = mapped_column(String(150), default='')
    unit: Mapped[str] = mapped_column(String(30), default='')
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14,2))
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5,2), default=Decimal('0'))
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5,4), default=Decimal('0.15'))
    line_total: Mapped[Decimal] = mapped_column(Numeric(16,2))


class DeliveryNote(TimeID, Base):
    """Supplier dispatch document; buyer confirmation of the same record is the goods receipt."""
    __tablename__ = 'delivery_notes'
    __table_args__ = (Index('ix_delivery_notes_po_status', 'purchase_order_id', 'status'),)
    reference: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    purchase_order_id: Mapped[str] = mapped_column(ForeignKey('purchase_orders.id'))
    supplier_org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'), index=True)
    buyer_org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'), index=True)
    # draft, dispatched, received, received_with_issues
    status: Mapped[str] = mapped_column(String(30), default='draft')
    delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    vehicle_no: Mapped[str] = mapped_column(String(60), default='')
    driver_name: Mapped[str] = mapped_column(String(150), default='')
    carrier: Mapped[str] = mapped_column(String(150), default='')
    delivery_address: Mapped[str] = mapped_column(String(300), default='')
    supplier_remarks: Mapped[str] = mapped_column(Text, default='')
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_by_user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    received_by_name: Mapped[str] = mapped_column(String(150), default='')
    receipt_notes: Mapped[str] = mapped_column(Text, default='')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text('CURRENT_TIMESTAMP'))


class DeliveryNoteLine(TimeID, Base):
    __tablename__ = 'delivery_note_lines'
    __table_args__ = (UniqueConstraint('delivery_note_id', 'po_line_id', name='uq_delivery_note_po_line'),)
    delivery_note_id: Mapped[str] = mapped_column(ForeignKey('delivery_notes.id', ondelete='CASCADE'), index=True)
    po_line_id: Mapped[str] = mapped_column(ForeignKey('purchase_order_lines.id'), index=True)
    delivered_quantity: Mapped[Decimal] = mapped_column(Numeric(14,3))
    remarks: Mapped[str] = mapped_column(String(500), default='')
    received_quantity: Mapped[Decimal | None] = mapped_column(Numeric(14,3), nullable=True)
    damaged_quantity: Mapped[Decimal] = mapped_column(Numeric(14,3), default=Decimal('0'))
    receipt_remarks: Mapped[str] = mapped_column(String(500), default='')


class TaxInvoice(TimeID, Base):
    __tablename__ = 'tax_invoices'
    __table_args__ = (
        UniqueConstraint('supplier_org_id', 'invoice_number', name='uq_tax_invoice_supplier_number'),
        Index('ix_tax_invoices_po_status', 'purchase_order_id', 'status'),
        Index('ix_tax_invoices_buyer_status', 'buyer_org_id', 'status'),
    )
    reference: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    invoice_number: Mapped[str] = mapped_column(String(60))
    purchase_order_id: Mapped[str] = mapped_column(ForeignKey('purchase_orders.id'))
    delivery_note_id: Mapped[str | None] = mapped_column(ForeignKey('delivery_notes.id'), nullable=True)
    supplier_org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'), index=True)
    buyer_org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'), index=True)
    status: Mapped[str] = mapped_column(String(30), default='draft')  # draft, submitted, accepted, disputed
    invoice_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(5), default='SAR')
    subtotal: Mapped[Decimal] = mapped_column(Numeric(16,2), default=Decimal('0'))
    discount_total: Mapped[Decimal] = mapped_column(Numeric(16,2), default=Decimal('0'))
    vat_total: Mapped[Decimal] = mapped_column(Numeric(16,2), default=Decimal('0'))
    total: Mapped[Decimal] = mapped_column(Numeric(16,2), default=Decimal('0'))
    bank_name: Mapped[str] = mapped_column(String(150), default='')
    account_name: Mapped[str] = mapped_column(String(150), default='')
    iban: Mapped[str] = mapped_column(String(40), default='')
    swift: Mapped[str] = mapped_column(String(20), default='')
    payment_terms: Mapped[str] = mapped_column(Text, default='')
    # pending_verification, matched, quantity_mismatch, price_mismatch, requires_review
    match_status: Mapped[str] = mapped_column(String(30), default='pending_verification', index=True)
    match_details: Mapped[dict] = mapped_column(JSON, default=dict)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    review_notes: Mapped[str] = mapped_column(Text, default='')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text('CURRENT_TIMESTAMP'))


class TaxInvoiceLine(TimeID, Base):
    __tablename__ = 'tax_invoice_lines'
    __table_args__ = (UniqueConstraint('invoice_id', 'po_line_id', name='uq_invoice_po_line'),)
    invoice_id: Mapped[str] = mapped_column(ForeignKey('tax_invoices.id', ondelete='CASCADE'), index=True)
    po_line_id: Mapped[str] = mapped_column(ForeignKey('purchase_order_lines.id'), index=True)
    description: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(30), default='')
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14,2))
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5,2), default=Decimal('0'))
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(5,4), default=Decimal('0.15'))
    line_total: Mapped[Decimal] = mapped_column(Numeric(16,2))


class DocumentExtraction(TimeID, Base):
    """Document AI job: an uploaded supplier quotation PDF and its reviewable extraction."""
    __tablename__ = 'document_extractions'
    __table_args__ = (Index('ix_document_extractions_org_status', 'org_id', 'status', 'created_at'),)
    org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'))
    rfq_id: Mapped[str | None] = mapped_column(ForeignKey('rfqs.id'), nullable=True, index=True)
    uploaded_by_user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    file_id: Mapped[str] = mapped_column(ForeignKey('stored_files.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')  # queued, processing, review, confirmed, failed
    method: Mapped[str | None] = mapped_column(String(30), nullable=True)  # text_layer, gemini_ocr
    extracted: Mapped[dict] = mapped_column(JSON, default=dict)
    confirmed_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confirmed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text('CURRENT_TIMESTAMP'))


class AuditEvent(TimeID, Base):
    __tablename__='audit_events'
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    org_id: Mapped[str | None] = mapped_column(ForeignKey('organizations.id'), nullable=True)
    action: Mapped[str] = mapped_column(String(120))
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

Index('ix_material_name_code', Material.name_en, Material.code)


# Lookup indexes used by account cleanup and administrator account listing.
Index('ix_users_created_id', User.created_at, User.id)
Index('ix_notifications_recipient_user', Notification.recipient_user_id)
Index('ix_import_batches_initiated_by', ImportBatch.initiated_by)
Index('ix_material_revisions_actor_user', MaterialRevision.actor_user_id)
Index('ix_audit_events_actor_user', AuditEvent.actor_user_id)
Index('ix_organization_claims_claimant', CompanyClaim.claimant_id)
Index('ix_organization_claims_reviewer', CompanyClaim.reviewer_id)
Index('ix_verifications_reviewer', Verification.reviewer_id)
Index('ix_contact_inquiries_handler', ContactInquiry.handled_by)
Index('ix_outbox_events_recipient_user', Outbox.payload['user_id'].as_string())
Index('ix_contact_inquiries_created_at', ContactInquiry.created_at)


# All ORM account writers (CLI, administration and application routes) share identity parsing.
# SQL writes are still protected by the canonical unique index and NOT NULL column.
from sqlalchemy import event, inspect as sa_inspect


@event.listens_for(User, "before_insert")
@event.listens_for(User, "before_update")
def canonical_user_phone(mapper, connection, target):
    from app.identity import normalize_saudi_phone
    import phonenumbers as pn

    state = sa_inspect(target)
    if state.pending or state.attrs.phone.history.has_changes():
        canonical = normalize_saudi_phone(target.phone, target.phone_country)
        target.phone_e164 = canonical
        region = pn.region_code_for_number(pn.parse(canonical, None))
        target.phone_country = region if region in pn.SUPPORTED_REGIONS else None

Index('uq_membership_recommended', MembershipPlan.recommended, unique=True,
      postgresql_where=text('recommended = true AND archived = false'),
      sqlite_where=text('recommended = 1 AND archived = 0'))
Index('ix_membership_public_order', MembershipPlan.publication_status, MembershipPlan.visible_on_website, MembershipPlan.display_order)
Index('ix_subscriptions_plan_id', Subscription.plan_id)

# Keep analytics indexes aligned with migration s29a5d04e781.
Index('ix_dashboard_org_created', Organization.created_at)
Index('ix_dashboard_rfq_created', RFQ.created_at)
Index('ix_dashboard_rfq_published', RFQ.published_at)
Index('ix_dashboard_quote_submitted', Quotation.submitted_at)
Index('ix_dashboard_verification_reviewed', Verification.reviewed_at)
Index('ix_dashboard_verification_org_status', Verification.org_id, Verification.status, Verification.reviewed_at)
Index('ix_dashboard_import_created', ImportBatch.created_at)
Index('ix_dashboard_outbox_event_status', Outbox.event, Outbox.status)
Index('ix_outbox_provider_message_id', Outbox.provider_message_id, postgresql_where=text('provider_message_id IS NOT NULL'), sqlite_where=text('provider_message_id IS NOT NULL'))


class SupplierPortalSession(TimeID, Base):
    """Restricted session exchanged for one invitation magic link.

    It authorizes only the invitation, RFQ and supplier organization of the link it
    was exchanged from. Only a SHA-256 hash of the session secret is stored; the secret
    lives in an HttpOnly cookie. ``verified_at`` records recipient verification (a
    one-time code sent to the destination the link was delivered to), required before
    a binding quotation is submitted.
    """
    __tablename__ = 'supplier_portal_sessions'
    __table_args__ = (Index('ix_supplier_portal_sessions_invitation', 'invitation_id', 'revoked_at'),)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    access_token_id: Mapped[str] = mapped_column(ForeignKey('supplier_access_tokens.id', ondelete='CASCADE'), index=True)
    invitation_id: Mapped[str] = mapped_column(ForeignKey('rfq_invitations.id', ondelete='CASCADE'))
    rfq_id: Mapped[str] = mapped_column(ForeignKey('rfqs.id', ondelete='CASCADE'))
    supplier_org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    verification_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
