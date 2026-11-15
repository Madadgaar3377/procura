"""Supplier account claiming, matching, and ownership-linking service."""

from __future__ import annotations

import re
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.identity import normalize_phone as normalize_business_phone
from app.models import (
    CompanyClaim,
    Contact,
    ContactChannel,
    Member,
    Notification,
    Organization,
    Outbox,
    User,
    utcnow,
)
from app.security import audit
from app.serializers import record

PUBLIC_EMAIL_DOMAINS = frozenset({
    'gmail.com',
    'googlemail.com',
    'yahoo.com',
    'yahoo.co.uk',
    'hotmail.com',
    'outlook.com',
    'live.com',
    'msn.com',
    'icloud.com',
    'me.com',
    'aol.com',
    'proton.me',
    'protonmail.com',
    'mail.com',
    'yandex.com',
    'zoho.com',
})


def _clean_cr(value: str | None) -> str:
    if not value:
        return ''
    return re.sub(r'[\s\-_]+', '', value).strip().casefold()


def _corporate_email_domain(user: User) -> str | None:
    if not user.email or not user.email_verified or '@' not in user.email:
        return None
    domain = user.email.rsplit('@', 1)[1].strip().casefold()
    if not domain or domain in PUBLIC_EMAIL_DOMAINS:
        return None
    return domain


def _safe_norm_phone(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return normalize_business_phone(value, 'SA')
    except ValueError:
        try:
            return normalize_business_phone(value)
        except ValueError:
            return None


def _org_phone_matches(db: Session, org: Organization, phone_e164: str) -> bool:
    if org.business_phone and _safe_norm_phone(org.business_phone) == phone_e164:
        return True
    if db.scalar(
        select(Contact.id).where(
            Contact.org_id == org.id,
            Contact.deleted_at.is_(None),
            Contact.merged_into_contact_id.is_(None),
            Contact.phone_normalized == phone_e164,
        )
    ):
        return True
    if db.scalar(
        select(ContactChannel.id).where(
            ContactChannel.org_id == org.id,
            ContactChannel.kind == 'phone',
            ContactChannel.normalized_value == phone_e164,
        )
    ):
        return True
    return False


def _org_email_domain_matches(db: Session, org: Organization, domain: str) -> bool:
    suffix = f'@{domain}'
    if org.business_email and org.business_email.strip().casefold().endswith(suffix):
        return True
    if db.scalar(
        select(Contact.id).where(
            Contact.org_id == org.id,
            Contact.deleted_at.is_(None),
            Contact.merged_into_contact_id.is_(None),
            Contact.email.ilike(f'%{suffix}'),
        )
    ):
        return True
    if db.scalar(
        select(ContactChannel.id).where(
            ContactChannel.org_id == org.id,
            ContactChannel.kind == 'email',
            ContactChannel.normalized_value.ilike(f'%{suffix}'),
        )
    ):
        return True
    return False


def detect_claim_match_signals(
    db: Session,
    user: User,
    org: Organization,
    cr_number: str | None = None,
    name_query: str | None = None,
) -> list[str]:
    """Return high-level match signals without exposing private contact values."""
    reasons: list[str] = []
    clean_input_cr = _clean_cr(cr_number)
    clean_org_cr = _clean_cr(org.cr_number)
    if clean_input_cr and clean_org_cr and clean_input_cr == clean_org_cr:
        reasons.append('cr_number')

    if user.phone_verified and user.phone_e164 and _org_phone_matches(db, org, user.phone_e164):
        reasons.append('phone')

    domain = _corporate_email_domain(user)
    if domain and _org_email_domain_matches(db, org, domain):
        reasons.append('email_domain')

    if name_query:
        tokens = [tok for tok in name_query.strip().casefold().split() if len(tok) >= 2]
        haystack = f'{org.name or ""} {org.trading_name or ""}'.casefold()
        if tokens and all(tok in haystack for tok in tokens):
            reasons.append('name')

    return reasons


def search_claimable_suppliers(
    db: Session,
    user: User,
    q: str = '',
    cr_number: str = '',
    limit: int = 20,
) -> list[dict]:
    """Search existing supplier organizations using safe public fields only."""
    limit = max(1, min(limit, 50))
    name_q = ' '.join((q or '').strip().split())
    cr_q = (cr_number or '').strip()
    clean_cr = _clean_cr(cr_q)

    verified_phone = user.phone_e164 if user.phone_verified and user.phone_e164 else None
    corp_domain = _corporate_email_domain(user)

    filters = []
    if name_q:
        tokens = [tok for tok in name_q.casefold().split() if len(tok) >= 2][:5]
        if tokens:
            token_clauses = [
                or_(
                    Organization.name_normalized.ilike(f'%{tok}%'),
                    Organization.name.ilike(f'%{tok}%'),
                    Organization.trading_name.ilike(f'%{tok}%'),
                )
                for tok in tokens
            ]
            from sqlalchemy import and_
            filters.append(and_(*token_clauses))
        else:
            filters.append(
                or_(
                    Organization.name.ilike(f'%{name_q}%'),
                    Organization.trading_name.ilike(f'%{name_q}%'),
                )
            )
    if cr_q:
        cr_clauses = [
            Organization.cr_number == cr_q,
            Organization.cr_number.ilike(f'%{cr_q}%'),
        ]
        if clean_cr:
            cr_clauses.append(Organization.cr_number == clean_cr)
        filters.append(or_(*cr_clauses))
    if verified_phone:
        phone_org_ids = select(Contact.org_id).where(
            Contact.deleted_at.is_(None),
            Contact.merged_into_contact_id.is_(None),
            Contact.phone_normalized == verified_phone,
        )
        channel_phone_org_ids = select(ContactChannel.org_id).where(
            ContactChannel.kind == 'phone',
            ContactChannel.normalized_value == verified_phone,
        )
        filters.append(
            or_(
                Organization.business_phone == verified_phone,
                Organization.id.in_(phone_org_ids),
                Organization.id.in_(channel_phone_org_ids),
            )
        )
    if corp_domain:
        suffix = f'@{corp_domain}'
        email_org_ids = select(Contact.org_id).where(
            Contact.deleted_at.is_(None),
            Contact.merged_into_contact_id.is_(None),
            Contact.email.ilike(f'%{suffix}'),
        )
        channel_email_org_ids = select(ContactChannel.org_id).where(
            ContactChannel.kind == 'email',
            ContactChannel.normalized_value.ilike(f'%{suffix}'),
        )
        filters.append(
            or_(
                Organization.business_email.ilike(f'%{suffix}'),
                Organization.id.in_(email_org_ids),
                Organization.id.in_(channel_email_org_ids),
            )
        )

    if not filters:
        return []

    candidates = db.scalars(
        select(Organization)
        .where(
            Organization.is_supplier.is_(True),
            Organization.is_active.is_(True),
            or_(*filters),
        )
        .order_by(Organization.name)
        .limit(100)
    ).all()

    org_ids = [o.id for o in candidates]
    owned_org_ids: set[str] = set()
    if org_ids:
        owned_org_ids = set(
            db.scalars(
                select(Member.organization_id).where(
                    Member.organization_id.in_(org_ids),
                    Member.role == 'owner',
                )
            ).all()
        )

    scored: list[tuple[int, dict]] = []
    for org in candidates:
        reasons = detect_claim_match_signals(
            db,
            user,
            org,
            cr_number=cr_q,
            name_query=name_q,
        )
        if not reasons:
            # Fallback if matched by raw CR substring or single char
            if clean_cr and _clean_cr(org.cr_number) == clean_cr:
                reasons.append('cr_number')
            elif name_q and name_q.casefold() in (org.name or '').casefold():
                reasons.append('name')
        if not reasons:
            continue

        score = 0
        if 'cr_number' in reasons:
            score += 100
        if 'email_domain' in reasons:
            score += 40
        if 'phone' in reasons:
            score += 30
        if 'name' in reasons:
            score += 20
        if org.source in {'imported', 'admin_created'}:
            score += 5

        scored.append((
            -score,
            {
                'id': org.id,
                'name': org.name,
                'trading_name': org.trading_name,
                'city': org.city,
                'region': org.region,
                'verified_status': org.verified_status,
                'has_owner': org.id in owned_org_ids,
                'match_reasons': reasons,
            },
        ))

    scored.sort(key=lambda item: (item[0], item[1]['name'].casefold()))
    return [item[1] for item in scored[:limit]]


def serialize_claim_details(
    db: Session,
    claims: list[CompanyClaim],
    *,
    include_claimant: bool = False,
    include_org_cr: bool = False,
) -> list[dict]:
    if not claims:
        return []
    org_ids = list({c.org_id for c in claims})
    orgs = {
        o.id: o
        for o in db.scalars(select(Organization).where(Organization.id.in_(org_ids))).all()
    }
    users: dict[str, User] = {}
    if include_claimant:
        user_ids = list({c.claimant_id for c in claims})
        users = {
            u.id: u
            for u in db.scalars(select(User).where(User.id.in_(user_ids))).all()
        }

    result: list[dict] = []
    for c in claims:
        data = record(c)
        org = orgs.get(c.org_id)
        data['org_name'] = org.name if org else ''
        data['org_city'] = org.city if org else None
        data['org_verified_status'] = org.verified_status if org else 'unverified'
        data['org_cr_number'] = (org.cr_number if (org and include_org_cr) else None)
        if include_claimant:
            u = users.get(c.claimant_id)
            data['claimant_username'] = u.username if u else None
            data['claimant_email'] = u.email if u else None
            data['claimant_phone'] = u.phone_e164 or u.phone if u else None
        else:
            data['claimant_username'] = None
            data['claimant_email'] = None
            data['claimant_phone'] = None
        result.append(data)
    return result


def decide_claim(
    db: Session,
    actor: User,
    claim_id: str,
    *,
    approve: bool,
    notes: str,
    assigned_role: str | None = None,
    require_org_id: str | None = None,
) -> CompanyClaim:
    """Atomically approve or reject a supplier ownership/linking claim."""
    item = db.get(CompanyClaim, claim_id)
    if not item or (require_org_id and item.org_id != require_org_id):
        raise HTTPException(404, 'Claim not found')

    # Lock organization first so concurrent claim approvals serialize on one ownership boundary.
    org = db.scalar(select(Organization).where(Organization.id == item.org_id).with_for_update())
    if not org:
        raise HTTPException(404, 'Organization not found')

    db.refresh(item, with_for_update=True)
    if item.status != 'pending':
        raise HTTPException(409, 'Claim has already been reviewed')

    now = utcnow()
    existing_owners = db.scalars(
        select(Member).where(Member.organization_id == item.org_id, Member.role == 'owner')
    ).all()
    had_owner = len(existing_owners) > 0

    item.status = 'approved' if approve else 'rejected'
    item.reviewer_id = actor.id
    item.reviewer_notes = notes.strip()
    item.reviewed_at = now

    if approve:
        if db.scalar(
            select(Member.id).where(
                Member.organization_id == item.org_id,
                Member.user_id == item.claimant_id,
            )
        ):
            raise HTTPException(409, 'Claimant is already a member of this supplier')

        if had_owner:
            # Competing claim on an already-owned supplier: default to manager unless owner/admin specifies role.
            role_to_assign = assigned_role or ('manager' if item.assigned_role == 'owner' else item.assigned_role)
            if not actor.is_admin and role_to_assign == 'owner' and not any(m.user_id == actor.id for m in existing_owners):
                raise HTTPException(403, 'Only an existing owner or administrator can grant owner role')
        else:
            role_to_assign = assigned_role or 'owner'

        if role_to_assign not in {'owner', 'manager', 'supplier', 'viewer'}:
            raise HTTPException(422, 'Invalid role assignment')

        item.assigned_role = role_to_assign
        member = Member(
            organization_id=item.org_id,
            user_id=item.claimant_id,
            role=role_to_assign,
        )
        db.add(member)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409, 'Membership already exists for this user and supplier') from exc

        # If this organization was previously unclaimed and now has an owner,
        # automatically reject any competing pending claims so concurrent approvals cannot create duplicate owners.
        if not had_owner and role_to_assign == 'owner':
            competing = db.scalars(
                select(CompanyClaim).where(
                    CompanyClaim.org_id == item.org_id,
                    CompanyClaim.id != item.id,
                    CompanyClaim.status == 'pending',
                )
            ).all()
            for other in competing:
                other.status = 'rejected'
                other.reviewer_id = actor.id
                other.reviewer_notes = 'Superseded by an approved ownership claim.'
                other.reviewed_at = now
                db.add(
                    Notification(
                        recipient_user_id=other.claimant_id,
                        event='organization.claim_rejected',
                        message=f'Your claim for {org.name} was closed because another ownership claim was approved.',
                        channel='in_app',
                        status='sent',
                    )
                )
                audit(db, actor, 'organization.claim_superseded', item.org_id, other.id)

        audit(
            db,
            actor,
            'organization.member_added',
            item.org_id,
            member.id,
            user_id=item.claimant_id,
            role=role_to_assign,
            via_claim_id=item.id,
        )

    audit(
        db,
        actor,
        'organization.claim_approved' if approve else 'organization.claim_rejected',
        item.org_id,
        item.id,
        approved=approve,
        claimant_id=item.claimant_id,
        assigned_role=item.assigned_role if approve else None,
        notes=item.reviewer_notes,
    )

    # Notify ONLY the claimant (never broadcast to imported suppliers).
    db.add(
        Notification(
            recipient_user_id=item.claimant_id,
            event='organization.claim_approved' if approve else 'organization.claim_rejected',
            message=(
                f'Your claim for {org.name} has been approved.'
                if approve
                else f'Your claim for {org.name} was rejected: {item.reviewer_notes}'
            )[:500],
            channel='in_app',
            status='sent',
        )
    )
    db.add(
        Outbox(
            event='organization.claim_decided',
            payload={
                'claim_id': item.id,
                'org_id': item.org_id,
                'claimant_id': item.claimant_id,
                'approved': approve,
                'assigned_role': item.assigned_role if approve else None,
            },
            dedupe_key=f'claim-decision:{item.id}:{item.status}',
        )
    )

    db.commit()
    db.refresh(item)
    return item
