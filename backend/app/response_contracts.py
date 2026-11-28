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

_UserColumns = contract('UserColumns', models.User, ('password_hash',))
class UserOut(_UserColumns):
    avatar_url: str | None = None

_OrgColumns = contract('OrganizationColumns', models.Organization, ('quote_automation',))
class OrganizationOut(_OrgColumns):
    logo_url: str | None = None
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
    main_category:str=''
    subcategory:str=''
    description:str
    material_code:str=''
    unit:str
    brand:str=''
    model:str=''
    unit_price:str
    min_order_quantity:str=''
    lead_days:str=''
    row_status:str='Active'
    notes:str=''
    material_id:str|None=None
    matched_material_code:str|None=None
    matched_material_name:str|None=None
    existing_price:str|None=None
    action:str='create'
    status:str
    issue_category:str|None=None
    issue:str|None=None
class RateImportOut(ResponseModel):
    batch_id:str
    filename:str=''
    status:str
    stage:str='parse'
    rows:int
    valid:int
    new_count:int=0
    update_count:int=0
    unmatched_count:int=0
    invalid_count:int=0
    duplicate_count:int=0
    conflict_count:int=0
    created:int=0
    updated_count:int=0
    skipped_count:int=0
    errors:int
    saved:int
    completed_rows:int
    current_job:int=0
    jobs_total:int=0
    current_batch:int=0
    batches_in_job:int=0
    progress_percent:int=0
    failure_reason:str|None=None
    items:list[RateImportRow]=Field(default_factory=list)
    limit:int=25
    offset:int=0

RFQOut = contract('RFQOut', models.RFQ, ('attachment_records','client_request_id'))
RFQItemOut = contract('RFQItemOut', models.RFQItem)
InvitationOut = contract('InvitationOut', models.Invitation, ('match_evidence', 'rfq_revision'))
MatchOut = contract('MatchOut', models.RFQMatch, ('rfq_revision',))
class MatchedItemDetail(ResponseModel):
    item_id: str
    description: str
    match_level: str | None = None
    discipline: str | None = None
    main_category: str | None = None
    subcategory: str | None = None

class MatchSummary(MatchOut):
    supplier_name: str
    covered_items: list[str]
    coverage: str
    verification_status: str | None = None
    description: str | None = None
    website: str | None = None
    business_email: str | None = None
    business_phone: str | None = None
    service_locations: list[str] = Field(default_factory=list)
    matched_categories: list[str] = Field(default_factory=list)
    invitation_status: str | None = None
    quotation_status: str | None = None
    # Actual delivery per channel (None when no invitation is recorded).
    email_status: str | None = None
    sms_status: str | None = None
    profile_url: str | None = None
    match_levels: list[str] = Field(default_factory=list)
    brand_requirement_status: str = 'unverified'
    service_coverage_status: str = 'unknown'
    invitation_review_required: bool = False
    # exact | product | capability | broad (broad matches are never auto-invited)
    match_strength: str | None = None
    matched_item_count: int = 0
    matched_items: list[MatchedItemDetail] = Field(default_factory=list)
    saved_price_available: bool = False


class InvitationSummary(InvitationOut):
    rfq_reference: str
    title: str
    item_descriptions: list[str]
QuotationOut = contract('QuotationOut', models.Quotation)
QuoteLineOut = contract('QuoteLineOut', models.QuoteLine, ('source_rate_snapshot',))
NotificationOut = contract('NotificationOut', models.Notification)
PlanOut = contract('PlanOut', models.MembershipPlan)
SubscriptionOut = contract('SubscriptionOut', models.Subscription)
VerificationOut = contract('VerificationOut', models.Verification)
ClaimOut = contract('ClaimOut', models.CompanyClaim)

class ClaimableSupplierMatch(ResponseModel):
    id: str
    name: str
    trading_name: str | None = None
    city: str | None = None
    region: str | None = None
    verified_status: str
    has_owner: bool
    match_reasons: list[str] = Field(default_factory=list)

class ClaimDetailsOut(ClaimOut):
    org_name: str = ''
    org_city: str | None = None
    org_cr_number: str | None = None
    org_verified_status: str = 'unverified'
    claimant_username: str | None = None
    claimant_email: str | None = None
    claimant_phone: str | None = None
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
    document: dict[str,Any] = Field(default_factory=dict)
    quotation: QuotationOut
    lines: list[QuoteLineOut]
    totals: Totals | None = None
    supplier_name: str | None = None
    rfq_reference: str | None = None
    favorite: bool = False
    attachments: list[RFQAttachmentOut] = Field(default_factory=list)

class QuotationSummary(QuotationOut):
    supplier_name: str
    rfq_reference: str
    rfq_title: str
    totals: Totals

class BuyerInvitation(InvitationOut):
    supplier_name: str
    quotation_status: str | None = None
    reminder_eligible: bool = False
    next_reminder_at: str | None = None
    # A recorded invitation is not a delivery: each channel reports its actual outcome
    # (not_queued, queued, sending, retry_scheduled, accepted, sent, delivered, failed,
    # hard_bounced, soft_bounced, suppressed, skipped, uncertain, simulated).
    invitation_recorded: bool = True
    email_status: str = 'not_queued'
    email_reason: str | None = None
    sms_status: str = 'not_queued'
    sms_reason: str | None = None
    supplier_response: str | None = None

class Comparison(ResponseModel):
    rfq_id: str
    quotations: list[QuoteDetail]
    weights: dict[str, int] = Field(default_factory=dict)
    ranking: list[dict[str, Any]] = Field(default_factory=list)
    method: str = ''
    currency: str = 'SAR'

class TaxonomyOption(ResponseModel):
    id: str
    name: str
    parent_id: str | None = None

class MatchFilterOptions(ResponseModel):
    disciplines: list[str] = Field(default_factory=list)
    main_categories: list[TaxonomyOption] = Field(default_factory=list)
    subcategories: list[TaxonomyOption] = Field(default_factory=list)

class MatchResults(ResponseModel):
    matches: list[MatchSummary]
    invitations: list[BuyerInvitation]
    progress: Literal['draft', 'published', 'matching_queued', 'matching_in_progress', 'matching_completed', 'matching_failed', 'invitations_available', 'matches_available', 'no_eligible_suppliers', 'closed']
    matching_status: Literal['queued', 'processing', 'completed', 'failed']
    # Buyer-safe cause when matching_status is 'failed' (codes only, no internals).
    matching_failure_code: Literal['no_matching_job', 'matching_stalled', 'matching_error'] | None = None
    matching_failure_reason: str | None = None
    # Display pagination/filtering only: totals for the current view and filters.
    page_total: int = 0
    invitation_total: int = 0
    invitation_limit: int = 25
    invitation_offset: int = 0
    sort: str = 'relevance'
    filter_options: MatchFilterOptions = Field(default_factory=lambda: MatchFilterOptions())
    pending_invitation_ids: list[str]
    matched_suppliers: int = 0
    total_candidates: int = 0
    invited_suppliers: int = 0
    pending_suppliers: int = 0
    suppliers_evaluated: int = 0
    quotations_received: int = 0
    explanation: str | None = None
    deliveries: dict[str, int] = Field(default_factory=dict)
    reminder_available_at: str | None = None
    reminders_enabled: bool | None = None
    reminders_disabled_code: str | None = None
    reminders_disabled_reason: str | None = None
    # Database totals across every page (strongest level per supplier).
    exact_material_matches: int = 0
    product_description_matches: int = 0
    subcategory_matches: int = 0
    main_category_matches: int = 0
    excluded_suppliers: int = 0
    excluded_by_status: dict[str, int] = Field(default_factory=dict)
    limit: int = 25
    offset: int = 0

class MatchingSupplierDiagnostic(ResponseModel):
    supplier_org_id: str
    supplier_name: str
    status: str
    reasons: list[str]

class MatchingItemDiagnostic(ResponseModel):
    item_id: str
    description: str
    material_id: str | None = None
    main_category_id: str | None = None
    main_category_name: str | None = None
    subcategory_id: str | None = None
    subcategory_name: str | None = None
    classified: bool
    matched_supplier_count: int = 0
    specifications_excerpt: str = ''
    material_master_main_category_id: str | None = None
    material_master_main_category_name: str | None = None
    material_master_subcategory_id: str | None = None
    material_master_subcategory_name: str | None = None
    classification_confidence: float | None = None
    classification_source: str | None = None
    classification_needs_confirmation: bool = False
    classification_conflict: bool = False
    classification_note: str | None = None
    product_candidates: list[str] = Field(default_factory=list)
    level_counts: dict[str, int] = Field(default_factory=dict)


class MatchingEligibleSupplierDiagnostic(ResponseModel):
    supplier_org_id: str
    supplier_name: str
    status: str
    score: int
    match_levels: list[str] = Field(default_factory=list)
    coverage: str = 'Not recorded'
    invitation_status: str | None = None
    quotation_status: str | None = None
    ai_assisted: bool = False
    reasons: list[str] = Field(default_factory=list)


class MatchingDiagnosticOut(ResponseModel):
    rfq_id: str
    rfq_items: int
    classified_items: int = 0
    unclassified_items: int = 0
    active_suppliers_checked: int
    category_candidate_organizations: int
    approved_matching_capabilities: int
    exact_matches: int = 0
    category_matches: int = 0
    ai_assisted_candidates: int = 0
    rejected_by_location: int
    rejected_by_verification_or_eligibility: int
    matches_created: int
    invitations_created: int
    product_matches: int = 0
    subcategory_matches: int = 0
    main_category_matches: int = 0
    matching_duration_ms: float | None = None
    delivery_summary: dict[str, int] = Field(default_factory=dict)
    invitation_statuses: dict[str, int] = Field(default_factory=dict)
    quotation_statuses: dict[str, int] = Field(default_factory=dict)
    item_diagnostics: list[MatchingItemDiagnostic] = Field(default_factory=list)
    eligible_suppliers: list[MatchingEligibleSupplierDiagnostic] = Field(default_factory=list)
    excluded_suppliers: list[MatchingSupplierDiagnostic] = Field(default_factory=list)
    job_status: list[OutboxOut]
    supplier_reasons: list[MatchingSupplierDiagnostic]
    # Candidate funnel from persisted data: initial candidates, discipline/main/sub
    # capability holders, exact/fuzzy product matches, exclusions, final eligible.
    funnel: dict[str, int] = Field(default_factory=dict)
    channel_delivery: dict[str, dict[str, int]] = Field(default_factory=dict)
    reminders: dict[str, Any] = Field(default_factory=dict)
    failed_job_reasons: list[str] = Field(default_factory=list)

class MatchingRerunOut(ResponseModel):
    rfq_id: str
    job_id: str
    job_status: Literal['pending','retry','processing','processed','failed']
    deduplicated: bool

class ReviewQueue(ResponseModel):
    verifications: list[VerificationOut]
    claims: list[ClaimDetailsOut]

class PublicCategory(ResponseModel):
    id: str
    name_en: str
    name_ar: str = ''
    trade_keywords: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    units: list[str] = Field(default_factory=list)


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
    logo_url: str | None = None
    main_categories: list[str] = Field(default_factory=list)
    main_categories_ar: list[str] = Field(default_factory=list)

class PublicSupplierPage(ResponseModel):
    items: list[PublicSupplier]
    total: int
    page: int
    limit: int


class PublicSupplierProduct(ResponseModel):
    id: str
    name: str
    name_ar: str = ''
    description: str = ''
    description_ar: str = ''
    brand: str = ''
    model: str = ''
    unit: str | None = None
    specifications: dict = Field(default_factory=dict)
    main_category_id: str
    main_category: str
    main_category_ar: str = ''
    subcategory_id: str | None = None
    subcategory: str | None = None
    subcategory_ar: str = ''
    source: Literal['material_master','supplier_description']

class PublicSupplierProductPage(ResponseModel):
    items: list[PublicSupplierProduct]
    total: int
    page: int
    limit: int

class IndustryMaterialPreview(ResponseModel):
    id: str
    name_en: str
    name_ar: str = ''
    description: str = ''
    description_ar: str = ''
    unit: str = ''

class IndustryDiscipline(ResponseModel):
    id: str
    name_en: str
    name_ar: str = ''
    category_count: int
    material_count: int

class IndustryCategoryCard(ResponseModel):
    id: str
    name_en: str
    name_ar: str = ''
    main_category_id: str
    main_category: str
    main_category_ar: str = ''
    subcategory_id: str | None = None
    subcategory: str | None = None
    subcategory_ar: str = ''
    material_count: int
    materials: list[IndustryMaterialPreview] = Field(default_factory=list)

class IndustryCatalogPage(ResponseModel):
    disciplines: list[IndustryDiscipline]
    categories: list[IndustryCategoryCard]
    total: int
    page: int
    limit: int

class IndustryTaxonomyReviewCandidate(ResponseModel):
    id: str
    code: str
    name_en: str
    name_ar: str = ''
    parent_id: str | None = None
    parent_name_en: str | None = None
    reason: str
    issue_type: str | None = None
    active_material_count: int = 0
    proposed_action: str | None = None
    safe_to_auto_fix: bool = False

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
