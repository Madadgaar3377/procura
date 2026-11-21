"""Contracts for buyer-owned staging jobs; these never create master materials."""
from decimal import Decimal
from typing import Any, Literal
from pydantic import Field
from app.schemas import Model, RFQLineIn


class RFQImportCandidate(Model):
    id:str
    description:str
    main_category_id:str
    subcategory_id:str|None=None
    category:str
    unit:str
    score:float


class RFQImportRow(Model):
    id:str
    row_number:int
    original_description:str
    description:str
    specifications:str=''
    main_category_id:str|None=None
    subcategory_id:str|None=None
    material_id:str|None=None
    unit:str
    quantity:str
    group:str=''
    attributes:dict[str,Any]=Field(default_factory=dict)
    candidates:list[RFQImportCandidate]=Field(default_factory=list)
    confidence:float=0
    status:str
    accepted:bool=False
    removed:bool=False
    issue:str|None=None
    ai_fallback:bool=False
    version:int=1


class RFQImportJob(Model):
    id:str
    status:str
    stage:str
    total:int
    processed:int
    matched:int
    needs_review:int
    unmatched:int
    failed:int
    ai_enabled:bool
    failure_reason:str|None=None
    headers:list[str]=Field(default_factory=list)
    column_mapping:dict[str,str]=Field(default_factory=dict)
    rows:list[RFQImportRow]=Field(default_factory=list)


class RFQImportOptions(Model):
    column_mapping:dict[str,str]=Field(default_factory=dict,max_length=8)


class RFQImportEdit(Model):
    expected_version:int=Field(ge=1)
    description:str=Field(min_length=3,max_length=4000)
    specifications:str=Field(default='',max_length=10000)
    unit:str=Field(min_length=1,max_length=30)
    quantity:Decimal=Field(gt=0,max_digits=14,decimal_places=3)
    main_category_id:str
    subcategory_id:str|None=None
    material_id:str|None=None
    accepted:bool=False
    removed:bool=False


class RFQImportRetry(Model):
    row_ids:list[str]=Field(default_factory=list,max_length=300)


class RFQImportReview(Model):
    action:Literal['accept_high_confidence','remove']
    row_ids:list[str]=Field(default_factory=list,max_length=300)


class RFQImportConfirmedLine(RFQLineIn):
    key:str


class RFQImportConfirmation(Model):
    batch_id:str
    lines:list[RFQImportConfirmedLine]
