"""24-hour automatic account approval eligibility and sweep service."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AuditEvent,
    CompanyClaim,
    Member,
    Organization,
    Outbox,
    PlatformSettings,
    User,
)
from app.security import as_utc

log = logging.getLogger('uvicorn.error')


def get_platform_settings(db: Session) -> PlatformSettings:
    settings = db.get(PlatformSettings, 'default')
    if not settings:
        settings = PlatformSettings(id='default', auto_approval_enabled=True, auto_approval_hours=24)
        db.add(settings)
        db.flush()
    return settings


def _has_duplicate_cr_conflict(db: Session, org: Organization) -> bool:
    if not org.cr_number:
        return False
    return bool(
        db.scalar(
            select(Organization.id).where(
                Organization.id != org.id,
                Organization.cr_number == org.cr_number,
                Organization.verified_status == 'verified',
                Organization.is_active.is_(True),
            ).limit(1)
        )
    )


def _user_eligible_for_auto_approval(db: Session, user: User, cutoff: datetime) -> tuple[bool, str | None]:
    if user.approval_status != 'pending':
        return False, 'not_pending'
    if not user.is_active:
        return False, 'inactive'
    if user.manual_review_required:
        return False, 'manual_review_required'
    if not user.pending_approval_since or as_utc(user.pending_approval_since) > cutoff:
        return False, 'before_cutoff'
    if not user.phone_verified or not user.phone_e164:
        return False, 'phone_unverified'
    if user.email and not user.email_verified:
        return False, 'email_unverified'
    if not user.username or len(user.username.strip()) < 3:
        return False, 'invalid_registration_details'

    # Unresolved ownership claim blocks auto-approval
    pending_claim = db.scalar(
        select(CompanyClaim.id).where(
            CompanyClaim.claimant_id == user.id,
            CompanyClaim.status == 'pending',
        ).limit(1)
    )
    if pending_claim:
        return False, 'unresolved_company_claim'

    # Check associated organizations for flags, rejections, suspensions, or duplicate CRs
    orgs = db.scalars(
        select(Organization)
        .join(Member, Member.organization_id == Organization.id)
        .where(Member.user_id == user.id)
    ).all()
    for org in orgs:
        if not org.is_active or org.verified_status in {'rejected', 'suspended'}:
            return False, f'org_{org.verified_status or "inactive"}'
        if org.manual_review_required:
            return False, 'org_manual_review_required'
        if _has_duplicate_cr_conflict(db, org):
            return False, 'duplicate_company_cr_conflict'

    return True, None


def run_auto_approval_sweep(db: Session, now: datetime | None = None, *, limit: int = 100) -> dict:
    """Find and atomically approve eligible pending accounts older than configured hours."""
    now = as_utc(now or datetime.now(UTC))
    settings = get_platform_settings(db)
    if not settings.auto_approval_enabled:
        return {'approved_users': 0, 'evaluated': 0, 'enabled': False}

    hours = max(1, int(settings.auto_approval_hours or 24))
    cutoff = now - timedelta(hours=hours)

    candidate_ids = db.scalars(
        select(User.id)
        .where(
            User.approval_status == 'pending',
            User.is_active.is_(True),
            User.manual_review_required.is_(False),
            User.phone_verified.is_(True),
            User.pending_approval_since.is_not(None),
            User.pending_approval_since <= cutoff,
        )
        .order_by(User.pending_approval_since, User.id)
        .limit(limit)
    ).all()

    approved_count = 0
    for user_id in candidate_ids:
        user = db.scalar(
            select(User)
            .where(User.id == user_id, User.approval_status == 'pending')
            .with_for_update(skip_locked=True)
        )
        if not user:
            continue
        eligible, _reason = _user_eligible_for_auto_approval(db, user, cutoff)
        if not eligible:
            continue

        user.approval_status = 'approved'
        user.approved_at = now
        user.approval_method = 'auto_24h'
        user.approval_reason = 'AUTO_APPROVED_AFTER_24_HOURS'

        # Update only self-registered organizations owned by this user; never grant
        # ownership of imported companies or auto-approve supplier capabilities.
        owned_orgs = db.scalars(
            select(Organization)
            .join(Member, Member.organization_id == Organization.id)
            .where(
                Member.user_id == user.id,
                Member.role == 'owner',
                Organization.source == 'registered',
                Organization.is_active.is_(True),
                Organization.manual_review_required.is_(False),
            )
            .with_for_update(skip_locked=True)
        ).all()
        for org in owned_orgs:
            if org.approved_at is None:
                org.approved_at = now
                org.approval_method = 'auto_24h'
            if org.is_buyer and not org.is_supplier and org.verified_status in {'unverified', 'pending'}:
                if not _has_duplicate_cr_conflict(db, org):
                    org.verified_status = 'verified'

        db.add(
            AuditEvent(
                actor_user_id=None,
                org_id=owned_orgs[0].id if owned_orgs else None,
                action='account.auto_approved',
                entity_id=user.id,
                metadata_json={
                    'actor': 'SYSTEM',
                    'reason': 'AUTO_APPROVED_AFTER_24_HOURS',
                    'approval_method': 'auto_24h',
                    'approved_at': now.isoformat(),
                },
            )
        )

        dedupe_key = f'account-approved:{user.id}'
        if not db.scalar(select(Outbox.id).where(Outbox.dedupe_key == dedupe_key).limit(1)):
            db.add(
                Outbox(
                    event='account.approved',
                    payload={'user_id': user.id, 'approval_method': 'auto_24h'},
                    dedupe_key=dedupe_key,
                )
            )

        db.commit()
        approved_count += 1
        log.info('Auto-approved account user_id=%s method=auto_24h', user.id)

    return {'approved_users': approved_count, 'evaluated': len(candidate_ids), 'enabled': True}
