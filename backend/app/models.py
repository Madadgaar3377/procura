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
        sqlite_where=text("verified_status = 'verified' AND cr_number IS NOT NULL"),
        postgresql_where=text("verified_status = 'verified' AND cr_number IS NOT NULL"),
    ),)
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
    is_buyer: Mapped[bool] = mapped_column(Boolean, default=True)
    is_supplier: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    quote_automation: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))

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
    permission_to_notify: Mapped[bool] = mapped_column(Boolean, default=False)

class ContactInquiry(TimeID, Base):
    __tablename__='contact_inquiries'
    name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(200), index=True)
    company: Mapped[str | None] = mapped_column(String(220), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    topic: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default='new', index=True)
    handled_by: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class CompanyClaim(TimeID, Base):
    __tablename__='organization_claims'
    org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'))
    claimant_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    evidence: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default='pending')
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)

class Verification(TimeID, Base):
    __tablename__='verifications'
    org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'))
    cr_number: Mapped[str] = mapped_column(String(80))
    document_key: Mapped[str | None] = mapped_column(String(400), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='pending')
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

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

class MeasurementUnit(TimeID, Base):
    __tablename__='measurement_units'
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name_en: Mapped[str] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

class Material(TimeID, Base):
    __tablename__='materials'
    __table_args__=(Index('ix_materials_category_sort','category_id','code'),Index('ix_materials_subcategory_sort','subcategory_id','code'))
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

class ImportedRow(TimeID, Base):
    __tablename__='import_rows'
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
    raw_brands: Mapped[str] = mapped_column(Text, default='')
    raw_type: Mapped[str] = mapped_column(String(150), default='')
    raw_location: Mapped[str] = mapped_column(String(150), default='')
    mapping_status: Mapped[str] = mapped_column(String(30), default='unreviewed')

class Capability(TimeID, Base):
    __tablename__='supplier_capabilities'
    __table_args__=(
        CheckConstraint("eligibility_status IN ('enabled','suspended','revoked','rejected')",name='ck_capability_eligibility'),
        Index('ix_capability_eligible_scope','category_id','city','eligibility_status','confirmed','accepts_rfqs'),
        UniqueConstraint('org_id','category_id','city',name='uq_capability_area'),
        Index('ix_capability_match_area','category_id','city','confirmed','accepts_rfqs'),
    )
    org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'), index=True)
    category_id: Mapped[str] = mapped_column(ForeignKey('categories.id'), index=True)
    city: Mapped[str] = mapped_column(String(100), index=True)
    qualifications: Mapped[list] = mapped_column(JSON, default=list)
    material_ids: Mapped[list] = mapped_column(JSON, default=list)
    trade_keywords: Mapped[str] = mapped_column(Text, default='')
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    accepts_rfqs: Mapped[bool] = mapped_column(Boolean, default=True)
    eligibility_status: Mapped[str] = mapped_column(String(20), default='enabled', server_default='enabled')
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

class PriceBookItem(TimeID, Base):
    __tablename__='price_book_items'
    __table_args__=(CheckConstraint('unit_price > 0',name='ck_private_rate_positive'),Index('uq_private_rate_active','org_id','material_id','price_basis',unique=True,sqlite_where=text('active = 1'),postgresql_where=text('active = true')),Index('ix_private_rate_org_active_material','org_id','active','material_id','price_basis'),Index('ix_private_rate_org_created','org_id','created_at','id'),Index('ix_private_rate_org_categories','org_id','main_category_id','subcategory_id'),Index('uq_private_custom_rate_active','org_id','custom_key','price_basis',unique=True,sqlite_where=text('active = 1 AND material_id IS NULL'),postgresql_where=text('active = true AND material_id IS NULL')),CheckConstraint("material_id IS NOT NULL OR (main_category_id IS NOT NULL AND length(trim(supplier_description)) >= 3 AND length(trim(unit)) > 0 AND custom_key IS NOT NULL)",name='ck_private_custom_rate_details'))
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
    lead_days: Mapped[int] = mapped_column(Integer, default=7)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    unit: Mapped[str] = mapped_column(String(30), default='')
    currency: Mapped[str] = mapped_column(String(5), default='SAR', server_default='SAR')
    price_basis: Mapped[str] = mapped_column(String(30), default='supply', server_default='supply')
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text('true'))
    offered_description: Mapped[str] = mapped_column(Text, default='', server_default='')
    remarks: Mapped[str] = mapped_column(Text, default='', server_default='')
    technical_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    material_version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text('CURRENT_TIMESTAMP'))

class RFQ(TimeID, Base):
    __tablename__='rfqs'
    public_display_authorized: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text('false'))
    reference: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    buyer_org_id: Mapped[str] = mapped_column(ForeignKey('organizations.id'), index=True)
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

class RFQItem(TimeID, Base):
    __tablename__='rfq_items'
    rfq_id: Mapped[str] = mapped_column(ForeignKey('rfqs.id'), index=True)
    material_id: Mapped[str | None] = mapped_column(ForeignKey('materials.id'), nullable=True)
    main_category_id: Mapped[str | None] = mapped_column(ForeignKey('categories.id'), nullable=True, index=True)
    subcategory_id: Mapped[str | None] = mapped_column(ForeignKey('categories.id'), nullable=True, index=True)
    description: Mapped[str] = mapped_column(Text)
    specifications: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(30))
    quantity: Mapped[Decimal] = mapped_column(Numeric(14,3))

class RFQMatch(TimeID, Base):
    __tablename__='rfq_matches'
    __table_args__=(UniqueConstraint('rfq_id','supplier_org_id',name='uq_match'),)
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

class Quotation(TimeID, Base):
    __tablename__='quotations'
    __table_args__=(UniqueConstraint('invitation_id',name='uq_quote_invitation'),)
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

class Notification(TimeID, Base):
    __tablename__='notifications'
    recipient_user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    event: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(String(500))
    channel: Mapped[str] = mapped_column(String(30), default='in_app')
    status: Mapped[str] = mapped_column(String(30), default='pending')
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_outbox_id: Mapped[str | None] = mapped_column(ForeignKey('outbox_events.id'), nullable=True, index=True)

class Outbox(TimeID, Base):
    __tablename__='outbox_events'
    __table_args__=(
        Index('uq_outbox_dedupe_key','dedupe_key',unique=True),
        Index('ix_outbox_status_next_attempt','status','next_attempt_at'),
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
