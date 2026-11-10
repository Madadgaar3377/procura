"""Response contracts derived from mapped columns; private fields are explicitly excluded."""
from decimal import Decimal
from typing import Any, Literal, get_args, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, create_model
from sqlalchemy import JSON, inspect

from app import models


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

def contract(name, model, exclude=()):
    fields = {}
    annotations = get_type_hints(model)
    for column in inspect(model).columns:
        if column.key in exclude:
            continue
        try:
            kind = column.type.python_type
        except NotImplementedError:
            kind = Any
        # SQLAlchemy reports JSON's python_type as dict even when the mapped
        # attribute is a list. Keep generated response schemas aligned with
        # the model annotation and the actual serialized value.
        if isinstance(column.type, JSON):
            mapped_args = get_args(annotations.get(column.key, Any))
            if mapped_args:
                kind = mapped_args[0]
        if kind is Decimal:
            kind = str
        if column.nullable:
            kind = kind | None
        fields[column.key] = (kind, ...)
    return create_model(name, __base__=ResponseModel, **fields)

UserOut = contract('UserOut', models.User, ('password_hash',))
OrganizationOut = contract('OrganizationOut', models.Organization, ('quote_automation',))
MaterialOut = contract('MaterialOut', models.Material, ('source_identity', 'import_batch_id', 'import_row_number'))
CategoryOut = contract('CategoryOut', models.Category)
class CategorySummary(CategoryOut):
    materials_count: int

class SubcategoryMaterialOut(ResponseModel):
    category: CategoryOut
    material: MaterialOut | None
CapabilityOut = contract('CapabilityOut', models.Capability)
PriceColumns = contract('PriceColumns', models.PriceBookItem, ('custom_key',))
class PriceOut(PriceColumns):
    capability_status: str = Field(default='approved',description='Current supplier category approval: approved, pending, or not_selected. Independent of rate activation.')
    main_category_id: str | None = None
    subcategory_id: str | None = None
    main_category_name: str = ''
    subcategory_name: str = ''
    material_description: str = ''
    status: str = 'active'

class PricePage(ResponseModel):
    items: list[PriceOut]
    total: int
    limit: int
    offset: int

class PricePolicyOut(ResponseModel):
    require_capability_approval: bool

class UnitOptionOut(ResponseModel):
    code: str
    name_en: str
    sort_order: int

from app.schemas import QuoteAutomationIn


class AutomationMaterial(ResponseModel):
    id:str
    description:str
    unit:str
class QuoteAutomationOut(QuoteAutomationIn):
    revision:int=0
    authorized_by:str|None=None
    authorized_at:str|None=None
    materials:list[AutomationMaterial]=Field(default_factory=list)
class RateHistory(ResponseModel):
    id:str
    created_at:str
    action:str
    before:dict[str,Any]|None=None
    after:dict[str,Any]|None=None
class RateImportRow(ResponseModel):
    row:int
    description:str
    unit:str
    unit_price:str
    material_id:str|None=None
    action:str='create'
    status:str
    issue:str|None=None
class RateImportOut(ResponseModel):
    batch_id:str
    status:str
    rows:int
    valid:int
    errors:int
    saved:int
    completed_rows:int
    failure_reason:str|None=None
    items:list[RateImportRow]=Field(default_factory=list)
    limit:int=25
    offset:int=0

RFQOut = contract('RFQOut', models.RFQ, ('attachment_records',))
RFQItemOut = contract('RFQItemOut', models.RFQItem)
InvitationOut = contract('InvitationOut', models.Invitation)
MatchOut = contract('MatchOut', models.RFQMatch)
class MatchSummary(MatchOut):
    supplier_name: str
    covered_items: list[str]
    coverage: str

class InvitationSummary(InvitationOut):
    rfq_reference: str
    title: str
    item_descriptions: list[str]
QuotationOut = contract('QuotationOut', models.Quotation)
QuoteLineOut = contract('QuoteLineOut', models.QuoteLine)
NotificationOut = contract('NotificationOut', models.Notification)
PlanOut = contract('PlanOut', models.MembershipPlan)
SubscriptionOut = contract('SubscriptionOut', models.Subscription)
VerificationOut = contract('VerificationOut', models.Verification)
ClaimOut = contract('ClaimOut', models.CompanyClaim)
ImportBatchOut = contract('ImportBatchOut', models.ImportBatch, ('source_storage_key', 'source_sha256', 'import_options', 'preview_metadata', 'failure_reason'))
MeasurementUnitOut = contract('MeasurementUnitOut', models.MeasurementUnit)
AuditOut = contract('AuditOut', models.AuditEvent)
ContactInquiryOut = contract('ContactInquiryOut', models.ContactInquiry)
MemberOut = contract('MemberOut', models.Member)
OutboxOut = contract('OutboxOut', models.Outbox, ('secret_ciphertext',))

class SupplierImportConfirmation(ResponseModel):
    batch_id: str
    status: Literal['queued']
    rows: int
    created: int
    unchanged: int
    errors: int
    completed_rows: int

class TokenPair(ResponseModel):
    access_token: str
    refresh_token: str
    token_type: str

class RFQAttachmentOut(ResponseModel):
    id: str
    filename: str
    content_type: str
    size: int


class RFQAttachmentRemovalOut(ResponseModel):
    removed: bool


class RFQDetail(ResponseModel):
    rfq: RFQOut
    items: list[RFQItemOut]
    attachments: list[RFQAttachmentOut] = Field(default_factory=list)

class Totals(ResponseModel):
    subtotal: str
    vat: str
    total: str
    currency: str

class QuoteDetail(ResponseModel):
    quotation: QuotationOut
    lines: list[QuoteLineOut]
    totals: Totals | None = None
    supplier_name: str | None = None
    rfq_reference: str | None = None
    favorite: bool = False

class QuotationSummary(QuotationOut):
    supplier_name: str
    rfq_reference: str
    rfq_title: str
    totals: Totals

class BuyerInvitation(InvitationOut):
    supplier_name: str
    quotation_status: str | None = None
    reminder_eligible: bool = False

class Comparison(ResponseModel):
    rfq_id: str
    quotations: list[QuoteDetail]

class MatchResults(ResponseModel):
    matches: list[MatchSummary]
    invitations: list[BuyerInvitation]
    progress: Literal['draft', 'matching_queued', 'matching_in_progress', 'matching_failed', 'invitations_available', 'no_eligible_suppliers', 'closed']
    pending_invitation_ids: list[str]
    matched_suppliers: int = 0
    invited_suppliers: int = 0
    pending_suppliers: int = 0
    deliveries: dict[str, int] = Field(default_factory=dict)
    reminder_available_at: str | None = None
    limit: int = 25
    offset: int = 0

class ReviewQueue(ResponseModel):
    verifications: list[VerificationOut]
    claims: list[ClaimOut]

class PublicCategory(ResponseModel):
    id: str
    name_en: str
    name_ar: str = ''


class PublicCapabilityGroup(PublicCategory):
    subcategories: list[PublicCategory] = Field(default_factory=list)


class CapabilityReviewItem(CapabilityOut):
    organization_name: str
    main_category_id: str
    main_category_name: str
    subcategory_id: str | None = None
    subcategory_name: str | None = None
    related_materials: list[str] = Field(default_factory=list)


class PublicCompanyDetails(ResponseModel):
    trading_name: str | None = None
    company_type: str | None = None
    description: str | None = None
    website: str | None = None
    business_phone: str | None = None
    business_email: str | None = None
    address: str | None = None
    region: str | None = None
    country: str | None = None


class PublicSupplier(ResponseModel):
    profile: PublicCompanyDetails | None = None
    capability_groups: list[PublicCapabilityGroup] = Field(default_factory=list)
    shared_subcategories: list[PublicCategory] = Field(default_factory=list)
    service_cities: list[str] = Field(default_factory=list)
    capability_labels: list[str] = Field(default_factory=list)
    capability_labels_ar: list[str] = Field(default_factory=list)
    id: str
    name: str
    city: str | None
    verified: bool
    main_categories: list[str] = Field(default_factory=list)
    main_categories_ar: list[str] = Field(default_factory=list)

class ReportActivity(ResponseModel):
    month: str
    rfqs: int
    invitations: int
    quotations_submitted: int

class ReportSummary(ResponseModel):
    workspace: Literal['buyer','supplier','admin']
    rfqs_created: int
    invitations: int
    invitations_accepted: int
    quotations_submitted: int
    quotation_drafts: int
    activity: list[ReportActivity]
    rfq_statuses: dict[str,int] = Field(default_factory=dict)
    invitation_statuses: dict[str,int] = Field(default_factory=dict)
    quotation_statuses: dict[str,int] = Field(default_factory=dict)
    recent_rfqs: list[RFQOut] = Field(default_factory=list)
    awaiting_response: int = 0
    closed_rfqs: int = 0
    active_rfqs: int = 0
    published_rfqs: int = 0
    matched_suppliers: int = 0
    upcoming_deadlines: list[RFQOut] = Field(default_factory=list)
    recent_notifications: list[NotificationOut] = Field(default_factory=list)
    recent_enquiries: list[dict[str,Any]] = Field(default_factory=list)
    recent_quotations: list[QuotationOut] = Field(default_factory=list, description='Up to six newest quotations owned by the supplier workspace, ordered by creation date and ID. Empty outside the supplier workspace.')
