"""Admin API endpoints for category review analysis, safe bulk fixes, and explicit decisions."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import CategoryReviewDecision, User
from app.modules.taxonomy.review_service import (
    BATCH_SIZE,
    JOB_SIZE,
    ReviewActor,
    apply_single_category_decision,
    build_analysis_payload,
    build_hierarchy_repair_preview,
    get_hierarchy_repair_job,
    run_bulk_approve_valid,
    run_bulk_auto_fix,
    start_hierarchy_repair_job,
)
from app.security import admin_user

router = APIRouter(prefix='/review', tags=['Category Review'])


class ReviewMaterialPreviewOut(BaseModel):
    id: str
    code: str
    name_en: str
    current_discipline: str | None = None
    inferred_discipline: str | None = None
    safe: bool
    evidence: str
    issue: str


class CategoryReviewItemOut(BaseModel):
    id: str
    code: str
    name_en: str
    name_ar: str = ''
    parent_id: str | None = None
    parent_name_en: str | None = None
    hierarchy_path: list[str] = Field(default_factory=list)
    proposed_path: list[str] = Field(default_factory=list)
    hierarchy_repair_action: str | None = None
    hierarchy_repair_safe: bool = False
    affected_capabilities_count: int = 0
    issue_type: str
    issue_label: str
    reason: str
    secondary_issues: list[str] = Field(default_factory=list)
    active_material_count: int = 0
    inactive_material_count: int = 0
    missing_discipline_count: int = 0
    unsupported_discipline_count: int = 0
    duplicate_ids: list[str] = Field(default_factory=list)
    duplicate_canonical_id: str | None = None
    duplicate_safe: bool = False
    proposed_action: str
    proposed_discipline: str | None = None
    safe_to_auto_fix: bool = False
    can_approve: bool = False
    can_retain_empty: bool = False
    can_merge: bool = False
    can_archive: bool = False
    requires_manual_review: bool = False
    evidence: list[str] = Field(default_factory=list)
    current_decision: str | None = None
    materials_preview: list[ReviewMaterialPreviewOut] = Field(default_factory=list)


class CategoryReviewAnalysisOut(BaseModel):
    total_pending: int
    total_filtered: int
    total_safe_auto_fix: int
    total_safe_hierarchy_repairs: int = 0
    total_hierarchy_conflicts: int = 0
    total_valid_approvable: int
    total_conflicts: int
    counts_by_issue_type: dict[str, int]
    counts_by_action: dict[str, int]
    preview_token: str
    batch_size: int
    job_size: int
    limit: int
    offset: int
    items: list[CategoryReviewItemOut]


class CategoryFixPreviewItemOut(BaseModel):
    category_id: str
    code: str
    name_en: str
    parent_name_en: str | None = None
    issue_type: str
    proposed_action: str
    proposed_discipline: str | None = None
    materials_to_update: int = 0
    duplicate_count: int = 0
    reason: str


class BulkCategoryFixIn(BaseModel):
    dry_run: bool = False
    category_ids: list[str] | None = Field(default=None, max_length=300)
    cursor_after_id: str | None = None
    job_limit: int = Field(default=JOB_SIZE, ge=1, le=JOB_SIZE)
    batch_size: int = Field(default=BATCH_SIZE, ge=1, le=BATCH_SIZE)


class BulkCategoryFixOut(BaseModel):
    dry_run: bool
    job_size: int
    batch_size: int
    batches_executed: int
    categories_processed: int
    categories_resolved: int
    materials_updated: int
    duplicates_merged: int
    parent_links_repaired: int
    skipped_ambiguous: int
    remaining_safe: int
    has_more: bool
    next_cursor: str | None = None
    preview_items: list[CategoryFixPreviewItemOut] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)


class BulkApproveValidIn(BaseModel):
    dry_run: bool = False
    include_empty: bool = True
    category_ids: list[str] | None = Field(default=None, max_length=300)
    cursor_after_id: str | None = None
    job_limit: int = Field(default=JOB_SIZE, ge=1, le=JOB_SIZE)
    batch_size: int = Field(default=BATCH_SIZE, ge=1, le=BATCH_SIZE)


class BulkApproveValidOut(BaseModel):
    dry_run: bool
    job_size: int
    batch_size: int
    batches_executed: int
    total_eligible: int = 0
    retained_empty: int
    approved: int
    approved_count: int = 0
    skipped_already_approved: int = 0
    remaining_valid: int
    excluded_count: int = 0
    excluded_by_issue_type: dict[str, int] = Field(default_factory=dict)
    has_more: bool
    next_cursor: str | None = None
    preview_items: list[CategoryFixPreviewItemOut] = Field(default_factory=list)
    excluded_preview_items: list[CategoryFixPreviewItemOut] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)


class HierarchyRepairItemOut(BaseModel):
    category_id: str
    code: str
    name_en: str
    parent_id: str | None = None
    parent_name_en: str | None = None
    grandparent_id: str | None = None
    grandparent_name_en: str | None = None
    current_path: list[str] = Field(default_factory=list)
    proposed_path: list[str] = Field(default_factory=list)
    repair_action: str
    target_parent_id: str | None = None
    target_category_id: str | None = None
    affected_materials_count: int = 0
    affected_capabilities_count: int = 0
    safe: bool = False
    status: str
    reason: str = ''


class HierarchyRepairPreviewOut(BaseModel):
    total_hierarchy_issues: int
    safe_repairs: int
    manual_review_count: int
    affected_materials: int
    affected_capabilities: int
    counts_by_action: dict[str, int] = Field(default_factory=dict)
    batch_size: int
    job_size: int
    items: list[HierarchyRepairItemOut] = Field(default_factory=list)


class HierarchyRepairJobIn(BaseModel):
    category_ids: list[str] | None = Field(default=None, max_length=300)
    job_limit: int = Field(default=JOB_SIZE, ge=1, le=JOB_SIZE)
    batch_size: int = Field(default=BATCH_SIZE, ge=1, le=BATCH_SIZE)
    async_mode: bool = True


class HierarchyRepairJobOut(BaseModel):
    job_id: str | None = None
    status: str
    total_records: int = 0
    completed_records: int = 0
    repaired_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    batches_executed: int = 0
    materials_updated: int = 0
    references_moved: int = 0
    remaining_safe: int = 0
    has_more: bool = False
    errors: list[dict] = Field(default_factory=list)
    failure_reason: str | None = None


class CategoryReviewDecisionIn(BaseModel):
    decision: Literal['approve', 'retain_empty', 'reclassify', 'merge_duplicate', 'archive']
    discipline: Literal['Civil', 'Electrical', 'Mechanical'] | None = None
    target_parent_id: str | None = None
    material_disciplines: dict[str, Literal['Civil', 'Electrical', 'Mechanical']] | None = None
    notes: str | None = Field(default=None, max_length=500)


class CategoryReviewDecisionOut(BaseModel):
    category_id: str
    decision: str
    materials_updated: int = 0
    merged_into_category_id: str | None = None
    archived_ids: list[str] = Field(default_factory=list)


class CategoryReviewHistoryItemOut(BaseModel):
    id: str
    category_id: str
    decision: str
    issue_type: str
    discipline: str | None = None
    merged_into_category_id: str | None = None
    actor_user_id: str | None = None
    notes: str | None = None
    details: dict = Field(default_factory=dict)
    is_current: bool
    created_at: datetime


@router.get('/analyze', response_model=CategoryReviewAnalysisOut)
def analyze_review_queue(
    issue_type: str | None = Query(default=None),
    conflicts_only: bool = Query(default=False),
    include_resolved: bool = Query(default=False),
    q: str = Query(default='', max_length=100),
    limit: int = Query(default=100, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    """Analyze all active categories and classify pending review issues without mutating records."""
    del user
    return build_analysis_payload(
        db,
        issue_type=issue_type,
        conflicts_only=conflicts_only,
        include_resolved=include_resolved,
        q=q,
        limit=limit,
        offset=offset,
    )


def _detach_actor(user: User, db: Session) -> ReviewActor:
    """Extract scalar actor fields before releasing the request session."""
    actor = ReviewActor(id=user.id, username=user.username or '')
    db.rollback()
    db.close()
    return actor


@router.post('/auto-fix', response_model=BulkCategoryFixOut)
def auto_fix_safe_records(
    payload: BulkCategoryFixIn,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    """Preview or apply safe category review fixes in short committed batches of 30 (up to 300/job)."""
    # Release the auth session before running batched short transactions so no
    # PostgreSQL session is ever held idle-in-transaction between batches.
    actor_snapshot = _detach_actor(user, db)
    return run_bulk_auto_fix(
        actor=actor_snapshot,
        dry_run=payload.dry_run,
        category_ids=payload.category_ids,
        cursor_after_id=payload.cursor_after_id,
        job_limit=payload.job_limit,
        batch_size=payload.batch_size,
        session_factory=SessionLocal,
    )


@router.post('/approve-valid', response_model=BulkApproveValidOut)
def approve_all_valid(
    payload: BulkApproveValidIn,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    """Preview or explicitly retain valid empty categories in short committed batches of 30."""
    actor_snapshot = _detach_actor(user, db)
    return run_bulk_approve_valid(
        actor=actor_snapshot,
        dry_run=payload.dry_run,
        include_empty=payload.include_empty,
        category_ids=payload.category_ids,
        cursor_after_id=payload.cursor_after_id,
        job_limit=payload.job_limit,
        batch_size=payload.batch_size,
        session_factory=SessionLocal,
    )


@router.post('/categories/{category_id}/decision', response_model=CategoryReviewDecisionOut)
def submit_category_decision(
    category_id: str,
    payload: CategoryReviewDecisionIn,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    """Record an explicit admin review decision (approve, retain_empty, reclassify, merge_duplicate, archive)."""
    return apply_single_category_decision(
        db,
        category_id=category_id,
        decision=payload.decision,
        actor=user,
        discipline=payload.discipline,
        target_parent_id=payload.target_parent_id,
        material_disciplines=payload.material_disciplines,
        notes=payload.notes,
    )


@router.get('/categories/{category_id}/history', response_model=list[CategoryReviewHistoryItemOut])
def category_review_history(
    category_id: str,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    """Return audit history of explicit review decisions for a category."""
    del user
    rows = db.scalars(
        select(CategoryReviewDecision)
        .where(CategoryReviewDecision.category_id == category_id)
        .order_by(CategoryReviewDecision.created_at.desc())
        .limit(100)
    ).all()
    return [
        {
            'id': r.id,
            'category_id': r.category_id,
            'decision': r.decision,
            'issue_type': r.issue_type,
            'discipline': r.discipline,
            'merged_into_category_id': r.merged_into_category_id,
            'actor_user_id': r.actor_user_id,
            'notes': r.notes,
            'details': r.details or {},
            'is_current': r.is_current,
            'created_at': r.created_at,
        }
        for r in rows
    ]


@router.get('/hierarchy-repairs/preview', response_model=HierarchyRepairPreviewOut)
def preview_hierarchy_repairs(
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    """Analyze all depth > 2 hierarchy issues and preview safe vs manual-review repairs without mutating data."""
    del user
    return build_hierarchy_repair_preview(db, limit=limit, offset=offset)


@router.post('/hierarchy-repairs', response_model=HierarchyRepairJobOut)
def apply_hierarchy_repairs(
    payload: HierarchyRepairJobIn,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    """Start (or resume) a durable batched hierarchy repair job (30/batch, up to 300/job).

    Returns immediately with a job_id when async_mode=True so long repairs never block HTTP requests.
    """
    actor_snapshot = _detach_actor(user, db)
    return start_hierarchy_repair_job(
        actor=actor_snapshot,
        category_ids=payload.category_ids,
        job_limit=payload.job_limit,
        batch_size=payload.batch_size,
        async_mode=payload.async_mode,
        session_factory=SessionLocal,
    )


@router.get('/hierarchy-repairs/jobs/{job_id}', response_model=HierarchyRepairJobOut)
def hierarchy_repair_job_status(
    job_id: str,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    """Return persisted progress for a running or completed hierarchy repair job."""
    del user
    return get_hierarchy_repair_job(db, job_id)
