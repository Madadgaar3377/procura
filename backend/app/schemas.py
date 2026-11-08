from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.identity import normalize_email, normalize_phone, normalize_saudi_phone, normalize_username, validate_password


class Model(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_validator('*',mode='after')
    @classmethod
    def validate_required_text(cls,value,info):
        # Do not transform secrets or free-form RFQ descriptions.
        if info.field_name=='unit' and not cls.model_fields['unit'].is_required():
            return value
        if info.field_name in {'name','name_en','title','city','description','unit','org_id','category_id','buyer_org_id','material_id'} and isinstance(value,str):
            if not value.strip():raise ValueError('This field must not be blank')
            minimum={'name':2,'title':3,'city':2,'description':3}.get(info.field_name,1)
            if len(value.strip())<minimum:raise ValueError(f'Enter at least {minimum} non-whitespace characters')
        return value


class RFQReminderIn(Model):
    # Omitted means all pending recipients, preserving the existing API contract.
    invitation_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)

class DatabaseConnectStatus(Model):
    connected: bool
    status: Literal['connected','disconnected']
    message: str
    error: str|None=None
    checked_at: datetime

class ContactInquiryCreate(Model):
    name: str = Field(min_length=2, max_length=150)
    email: str = Field(max_length=200)
    company: str | None = Field(default=None, max_length=220)
    phone: str | None = Field(default=None, max_length=30)
    topic: Literal['buyer','supplier','partnership','support','other']
    message: str = Field(min_length=20, max_length=5000)
    consent: Literal[True]

    @field_validator('email')
    @classmethod
    def validate_email(cls, value):
        import re
        value = value.strip().casefold()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            raise ValueError('Invalid email address')
        return value

class ContactInquiryStatus(Model):
    status: Literal['reviewed','resolved']

class UserRegister(Model):
    model_config = ConfigDict(from_attributes=True,extra='forbid')
    username: str = Field(min_length=3,max_length=80)
    phone_country: str | None = Field(default=None,min_length=2,max_length=2)
    phone: str = Field(min_length=1,max_length=80)
    password: str = Field(min_length=6,max_length=128)
    email: str|None = Field(default=None,max_length=200)

    _username = field_validator('username')(normalize_username)
    @model_validator(mode='after')
    def canonical_phone(self):
        self.phone = normalize_saudi_phone(self.phone, self.phone_country, sms=True)
        return self
    _password = field_validator('password')(validate_password)

    @field_validator('email')
    @classmethod
    def validate_email(cls,value):
        if value is None:
            return value
        return normalize_email(value)

class Login(Model):
    model_config = ConfigDict(from_attributes=True,extra='forbid')
    phone_country: str | None = Field(default=None,min_length=2,max_length=2)
    identifier: str=Field(min_length=1,max_length=200)
    password: str=Field(min_length=1,max_length=128)

class BusinessProfile(Model):
    website: str|None=Field(default=None,max_length=300)
    trading_name: str|None=Field(default=None,max_length=220)
    company_type: Literal['supplier','contractor','both']|None=None
    description: str|None=Field(default=None,max_length=4000)
    contact_name: str|None=Field(default=None,max_length=150)
    designation: str|None=Field(default=None,max_length=100)
    business_email: str|None=Field(default=None,max_length=200)
    business_phone: str|None=Field(default=None,max_length=30)
    country: str|None=Field(default=None,max_length=100)
    region: str|None=Field(default=None,max_length=100)
    address: str|None=Field(default=None,max_length=300)
    vat_number: str|None=Field(default=None,pattern=r'^[0-9]{15}$',description='Optional VAT number: 15 digits. Spaces and Arabic-Indic digits are normalized; blank clears the value.')

    @field_validator('vat_number',mode='before')
    @classmethod
    def profile_vat(cls,value):
        if value is None or not isinstance(value,str):return value
        value=''.join(value.split()).translate(str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹','01234567890123456789'))
        if not value:return None
        if len(value)!=15 or not value.isascii() or not value.isdigit():
            raise ValueError('VAT number must contain exactly 15 digits, or be left blank if not VAT registered.')
        return value

    @field_validator('business_email')
    @classmethod
    def profile_email(cls,value):
        return normalize_email(value) if value else None

    @field_validator('business_phone')
    @classmethod
    def profile_phone(cls,value):
        # Optional business contacts are not authentication or OTP identities.
        return normalize_phone(value) if value else None

    @field_validator('website')
    @classmethod
    def profile_website(cls,value):
        from urllib.parse import urlparse
        if not value:return None
        parsed=urlparse(value)
        if parsed.scheme not in {'http','https'} or not parsed.hostname:
            raise ValueError('Website must be a valid HTTP or HTTPS URL')
        return value

class OrgCreate(BusinessProfile):
    model_config = ConfigDict(from_attributes=True,extra='forbid')
    name: str=Field(min_length=2,max_length=220)
    cr_number: str|None=Field(default=None,max_length=80)
    city: str|None=Field(default=None,max_length=100)
    is_buyer: bool=True
    is_supplier: bool=False
    website: str|None=Field(default=None,max_length=300)

class AdminSupplierCreate(Model):
    name: str=Field(min_length=2,max_length=220)
    contact_name: str|None=Field(default=None,min_length=2,max_length=150)
    phone: str|None=Field(default=None,min_length=8,max_length=30)
    email: str|None=Field(default=None,max_length=200)
    cr_number: str|None=Field(default=None,max_length=80)
    city: str|None=Field(default=None,max_length=100)
    website: str|None=Field(default=None,max_length=300)

class OrgUpdate(BusinessProfile):
    model_config = ConfigDict(from_attributes=True,extra='forbid')
    name: str|None=Field(default=None,min_length=2,max_length=220)
    city: str|None=Field(default=None,max_length=100)
    website: str|None=Field(default=None,max_length=300)
    is_buyer: bool|None=None
    is_supplier: bool|None=None

class VerifyCreate(Model):
    cr_number: str=Field(min_length=4,max_length=80)
    document_key: str|None=None

class Decision(Model):
    approve: bool
    notes: str=Field(min_length=4,max_length=500)

class ClaimCreate(Model):
    evidence: str=Field(min_length=15,max_length=4000)

class CategoryIn(Model):
    code:str|None=None
    name_en:str=Field(min_length=1,max_length=180)
    name_ar:str=''
    parent_id:str|None=None
    sort_order:int=0

class SubcategoryMaterialIn(CategoryIn):
    parent_id:str
    material_description:str|None=Field(default=None,max_length=250)
    unit:str|None=Field(default=None,max_length=30)

    @model_validator(mode='after')
    def require_material_pair(self):
        self.material_description=(self.material_description or '').strip() or None
        self.unit=(self.unit or '').strip() or None
        if bool(self.material_description)!=bool(self.unit):
            raise ValueError('Provide both Material Description and Unit, or leave both empty to save only the subcategory')
        if self.material_description and len(' '.join(self.material_description.split()))<3:
            raise ValueError('Material Description must contain at least 3 characters')
        return self

class CategoryEdit(CategoryIn):
    material_id: str|None=None
    material_subcategory_id: str|None=None
    material_description: str|None=Field(default=None,max_length=250)
    unit: str|None=Field(default=None,max_length=30)

    @model_validator(mode='after')
    def require_material_pair(self):
        self.material_description=(self.material_description or '').strip() or None
        self.unit=(self.unit or '').strip() or None
        if bool(self.material_description)!=bool(self.unit) or (self.material_id and not self.material_description):
            raise ValueError('Provide both Material Description and Unit when editing a material')
        if self.material_description and len(self.material_description)<3:
            raise ValueError('Material Description must contain at least 3 characters')
        return self

class MaterialIn(Model):
    code:str
    category_id:str
    name_en:str
    name_ar:str=''
    description:str=''
    unit:str='No.'
    specs_template:dict[str,Any]=Field(default_factory=dict)

class MaterialMasterIn(Model):
    main_category_id:str
    subcategory_id:str|None=None
    description:str=Field(min_length=3,max_length=250)
    unit:str=Field(min_length=1,max_length=30)
    code:str|None=Field(default=None,min_length=1,max_length=100)
    source_sn:str|None=Field(default=None,max_length=120)
    expenditure_code:str|None=Field(default=None,max_length=120)
    specifications:str=Field(default='',max_length=10_000)
    arabic_description:str=Field(default='',max_length=250)
    metadata:dict[str,Any]=Field(default_factory=dict)

class MeasurementUnitIn(Model):
    code:str=Field(min_length=1,max_length=30)
    name_en:str=Field(min_length=1,max_length=80)
    sort_order:int=0

class MaterialFilterPage(Model):
    items:list[dict[str,Any]]
    total:int
    limit:int
    offset:int
    filters:dict[str,Any]=Field(default_factory=dict)

class MaterialRevisionOut(Model):
    id:str
    material_id:str
    version:int
    actor_user_id:str|None
    action:str
    snapshot:dict[str,Any]
    created_at:datetime

class MaterialImportSheet(Model):
    name: str
    eligible: bool
    header_row: int | None = None
    row_count: int = 0
    headers: list[str] = Field(default_factory=list)
    reason: str | None = None

class MaterialImportPreview(Model):
    batch_id: str | None = None
    status: str
    discipline: str | None = None
    sheets: list[MaterialImportSheet] = Field(default_factory=list)
    selected_sheet: str | None = None
    headers: list[str] = Field(default_factory=list)
    mapping: dict[str,str] = Field(default_factory=dict)
    rows: int = 0
    valid: int = 0
    invalid: int = 0
    create: int = 0
    update: int = 0
    unchanged: int = 0
    duplicate_rows: int = 0
    created: int = 0
    updated_count: int = 0
    unchanged_count: int = 0
    categories_created: int = 0
    completed_rows: int = 0
    progress_percent: int = 0
    stage: str | None = None
    failure_reason: str | None = None
    new_categories: list[dict[str,Any]] = Field(default_factory=list)
    upload_duration_ms: int = 0
    parse_duration_ms: int = 0
    apply_duration_ms: int = 0
    updated_at: datetime | None = None
    sample: list[dict[str,Any]] = Field(default_factory=list)
    issues: list[dict[str,Any]] = Field(default_factory=list)
    warnings: list[dict[str,Any]] = Field(default_factory=list)

class MaterialImportResult(Model):
    batch_id: str
    status: str
    rows: int
    created: int
    updated: int
    unchanged: int
    errors: int
    categories_created: int

class CategoryImportSheet(Model):
    name: str
    eligible: bool
    header_row: int | None = None
    row_count: int = 0
    headers: list[str] = Field(default_factory=list)
    reason: str | None = None

class CategoryImportPreview(Model):
    batch_id: str | None = None
    status: str
    discipline: str | None = None
    sheets: list[CategoryImportSheet] = Field(default_factory=list)
    selected_sheet: str | None = None
    headers: list[str] = Field(default_factory=list)
    mapping: dict[str,str] = Field(default_factory=dict)
    rows: int = 0
    valid: int = 0
    invalid: int = 0
    create: int = 0
    unchanged: int = 0
    duplicate_rows: int = 0
    categories_to_create: int = 0
    arabic_labels_to_add: int = 0
    sample: list[dict[str,Any]] = Field(default_factory=list)
    issues: list[dict[str,Any]] = Field(default_factory=list)

class CategoryImportResult(Model):
    batch_id: str
    status: str
    rows: int
    categories_created: int
    unchanged: int
    errors: int
    arabic_labels_added: int = 0

class MaterialSearchPage(Model):
    items: list[dict[str,Any]]
    total: int
    limit: int
    offset: int

class CapabilitySelection(Model):
    org_id: str
    main_category_ids: list[str]=Field(max_length=100)
    subcategory_ids: list[str]=Field(max_length=500)
    city: str=Field(min_length=2,max_length=100)

class CapabilityIn(Model):
    org_id:str
    category_id:str
    city:str=Field(min_length=2,max_length=100)
    qualifications:list[str]=Field(default_factory=list)
    material_ids:list[str]=Field(default_factory=list,max_length=500)
    trade_keywords:str=''
    confirmed:bool=True

class CapabilityEligibilityIn(Model):
    eligibility_status:Literal['enabled','suspended','revoked','rejected']

class CapabilityReviewIn(Model):
    approve:bool
    rejection_reason:str=Field(default='',max_length=1000)

class PriceIn(Model):
    org_id:str
    material_id:str|None=None
    main_category_id:str|None=None
    subcategory_id:str|None=None
    supplier_description:str=Field(default='',max_length=4000)
    unit:str|None=Field(default=None,min_length=1,max_length=30)
    brand:str=Field(default='',max_length=150)
    model:str=Field(default='',max_length=150)
    offered_specs:str=Field(default='',max_length=10000)
    offered_description:str=Field(default='',max_length=4000)
    remarks:str=Field(default='',max_length=4000)
    unit_price:Decimal=Field(gt=0,max_digits=14,decimal_places=2)
    currency:Literal['SAR']='SAR'
    price_basis:Literal['supply','supply_install']='supply'
    lead_days:int=Field(default=7,ge=0,le=3650)
    valid_until:datetime|None=None
    technical_confirmed:bool=False
    active:bool=Field(default=True,description='Private rates can use saved self-declared categories. Capability confirmation is independent from rate activation.')
    expected_version:int|None=Field(default=None,ge=1)

    @model_validator(mode='after')
    def custom_details(self):
        self.supplier_description=self.supplier_description.strip()
        if not self.material_id and (len(self.supplier_description)<3 or not self.main_category_id or not self.subcategory_id or not self.unit):
            raise ValueError('Custom rates require main category, subcategory, supplier description (3+ characters) and unit')
        return self

    @field_validator('valid_until')
    @classmethod
    def require_timezone(cls,value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError('valid_until must include a timezone')
        return value

class RateStatus(Model):
    active:bool
    expected_version:int|None=Field(default=None,ge=1)

class QuoteAutomationIn(Model):
    enabled:bool=False
    material_ids:list[str]=Field(default_factory=list,max_length=500)
    service_cities:list[str]=Field(default_factory=list,max_length=50)
    price_basis:Literal['supply','supply_install']='supply'
    vat_rate:Decimal=Field(default=Decimal('0.15'),ge=0,le=1,decimal_places=4)
    max_quantity:Decimal|None=Field(default=None,gt=0,max_digits=14,decimal_places=3)
    max_total:Decimal|None=Field(default=None,gt=0,max_digits=14,decimal_places=2)
    valid_until:datetime|None=None
    confirm_terms:bool=False

    @model_validator(mode='after')
    def require_authorization(self):
        self.material_ids=list(dict.fromkeys(self.material_ids));self.service_cities=list(dict.fromkeys(city.strip() for city in self.service_cities if city.strip()))
        if self.enabled and not (self.material_ids and self.service_cities and self.max_quantity and self.max_total and self.valid_until and self.confirm_terms):
            raise ValueError('Automatic submission requires material coverage, service cities, quantity/total limits, expiry and explicit authorization')
        if self.valid_until and (self.valid_until.tzinfo is None or self.valid_until.utcoffset() is None):raise ValueError('Policy expiry must include a timezone')
        return self

class RateImportMapping(Model):
    material_id:str

class RFQLineIn(Model):
    material_id:str|None=None
    main_category_id:str|None=None
    subcategory_id:str|None=None
    description:str=Field(min_length=3,max_length=4000)
    specifications:str=Field(default='',max_length=10000)
    unit:str=Field(min_length=1,max_length=30)
    quantity:Decimal=Field(gt=0,max_digits=14,decimal_places=3)

class RFQIn(Model):
    buyer_org_id:str
    title:str=Field(min_length=3,max_length=250)
    city:str=Field(min_length=2,max_length=120)
    response_deadline:datetime
    items:list[RFQLineIn]=Field(min_length=1,max_length=100)
    required_qualifications:list[str]=Field(default_factory=list,max_length=50)
    project_name:str=Field(default='',max_length=250)
    required_delivery_date:date|None=None
    additional_instructions:str=Field(default='',max_length=10000)

    @model_validator(mode='after')
    def validate_delivery_date(self):
        if self.required_delivery_date and self.required_delivery_date < self.response_deadline.date():
            raise ValueError('Required delivery date must not precede the quotation deadline')
        return self

    @field_validator('response_deadline')
    @classmethod
    def require_deadline_timezone(cls,value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('response_deadline must include a timezone')
        return value

class QuoteLineIn(Model):
    offered_description:str=Field(default='',max_length=4000)
    rfq_item_id:str
    brand:str=Field(default='',max_length=150)
    model:str=Field(default='',max_length=150)
    offered_specs:str=Field(default='',max_length=10000)
    compliance:Literal['compliant','partial','noncompliant','unconfirmed']='unconfirmed'
    quantity:Decimal=Field(gt=0,max_digits=14,decimal_places=3)
    unit_price:Decimal=Field(ge=0,max_digits=14,decimal_places=2)
    lead_days:int=Field(ge=0,le=3650)
    note:str=Field(default='',max_length=4000)

class QuoteIn(Model):
    lines:list[QuoteLineIn]=Field(min_length=1,max_length=100)
    vat_rate:Decimal=Field(default=Decimal('0.15'),ge=0,le=1)
    price_basis:str='supply'
