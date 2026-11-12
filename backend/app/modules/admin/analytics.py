"""Read-only platform aggregates. All dates use UTC; no contact data is returned."""
from datetime import UTC, datetime, timedelta
from enum import IntEnum

from fastapi import Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (Organization, RFQ, Quotation, Invitation, Capability,
                        ImportBatch, Verification, CompanyClaim, Outbox, RFQMatch,
                        ImportedSupplierTrade, User)
from app.security import admin_user
from app.response_cache import cached_get
from .router import router


def dashboard_analytics(db: Session, days: int):
    now = datetime.now(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days-1)
    end = start + timedelta(days=days)

    def count(model, *conditions):
        return int(db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0)

    def groups(model, field):
        return dict(db.execute(select(field, func.count()).select_from(model).group_by(field)).all())

    pending = select(Verification.org_id).where(Verification.status == 'pending').union(
        select(CompanyClaim.org_id).where(CompanyClaim.status == 'pending'))
    pending_count = int(db.scalar(select(func.count()).select_from(pending.subquery())) or 0)
    statuses = groups(RFQ, RFQ.status)
    invitations = groups(Invitation, Invitation.status)
    quotes = count(Quotation, (Quotation.submitted_at.is_not(None)) | (Quotation.status == 'submitted'))
    kpis = dict(companies=count(Organization), verified_suppliers=count(Organization,
        Organization.is_supplier.is_(True), Organization.verified_status == 'verified'),
        active_rfqs=sum(statuses.get(s, 0) for s in ('published', 'collecting_quotes', 'review')),
        quotes_submitted=quotes, invitations=sum(invitations.values()),
        supplier_capabilities=count(Capability), imports=count(ImportBatch), pending_reviews=pending_count)

    series = { (start+timedelta(days=i)).date().isoformat(): dict(date=(start+timedelta(days=i)).date().isoformat(),
               rfqs_created=0, rfqs_published=0, suppliers_created=0, suppliers_verified=0) for i in range(days) }

    def daily(model, timestamp, key, *conditions, distinct=None):
        # date() works on SQLite test fixtures; PostgreSQL explicitly groups in UTC.
        day = (cast(func.timezone('UTC', timestamp), Date) if db.bind.dialect.name == 'postgresql'
               else func.date(timestamp))
        amount = func.count(func.distinct(distinct)) if distinct is not None else func.count()
        query = select(day, amount).select_from(model).where(timestamp >= start, timestamp < end, *conditions).group_by(day)
        for date, value in db.execute(query):
            if str(date) in series:
                series[str(date)][key] = int(value)

    daily(RFQ, RFQ.created_at, 'rfqs_created')
    daily(RFQ, RFQ.published_at, 'rfqs_published')
    daily(Organization, Organization.created_at, 'suppliers_created', Organization.is_supplier.is_(True))
    # First recorded approval per supplier, not registration time or repeated reviews.
    approvals = select(Verification.org_id, func.min(Verification.reviewed_at).label('approved_at')).join(
        Organization, Organization.id == Verification.org_id).where(
        Organization.is_supplier.is_(True), Verification.status == 'approved',
        Verification.reviewed_at.is_not(None)).group_by(Verification.org_id).subquery()
    daily(approvals, approvals.c.approved_at, 'suppliers_verified')

    completed = select(Outbox.payload['rfq_id'].as_string()).where(
        Outbox.event == 'rfq.published', Outbox.status == 'processed')
    eligible = select(RFQMatch.rfq_id).where(RFQMatch.status.in_(('eligible', 'partial')))
    zero = count(RFQ, RFQ.id.in_(completed), RFQ.id.not_in(eligible))
    matching = dict(completed=count(RFQ, RFQ.id.in_(completed)), zero_eligible=zero,
                    invitations_created=kpis['invitations'], accepted=invitations.get('accepted', 0),
                    declined=invitations.get('declined', 0), quotations_received=quotes)
    alerts = [dict(key='pending_reviews', count=pending_count, href='/app/admin/verification'),
        dict(key='failed_imports', count=count(ImportBatch, ImportBatch.status == 'failed'), href='/app/admin/bulk-import'),
        dict(key='pending_mappings', count=count(ImportedSupplierTrade, ImportedSupplierTrade.mapping_status == 'unreviewed'), href='/app/admin/suppliers'),
        dict(key='failed_matching', count=count(Outbox, Outbox.event == 'rfq.published', Outbox.status == 'failed'), href='/app/admin/matching'),
        dict(key='zero_eligible', count=zero, href='/app/admin/matching')]
    activity = []

    def recent(query, kind, href):
        for identity, label, status, timestamp in db.execute(query.limit(5)):
            timestamp = timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp
            activity.append(dict(id=f'{kind}:{identity}', kind=kind, label=label, status=status,
                                 timestamp=timestamp.isoformat(), href=href(identity)))

    recent(select(Organization.id, Organization.name, Organization.verified_status, Organization.created_at).where(
        Organization.is_supplier.is_(True)).order_by(Organization.created_at.desc(), Organization.id),
        'supplier', lambda _: '/app/admin/suppliers')
    recent(select(RFQ.id, RFQ.reference, RFQ.status, RFQ.created_at).order_by(RFQ.created_at.desc(), RFQ.id),
        'rfq', lambda id: f'/app/admin/rfqs/{id}')
    recent(select(Quotation.id, RFQ.reference, Quotation.status, Quotation.submitted_at).join(RFQ, RFQ.id == Quotation.rfq_id).where(
        Quotation.submitted_at.is_not(None)).order_by(Quotation.submitted_at.desc(), Quotation.id),
        'quotation', lambda _: '/app/admin/quotations')
    recent(select(ImportBatch.id, ImportBatch.filename, ImportBatch.status, ImportBatch.created_at).order_by(
        ImportBatch.created_at.desc(), ImportBatch.id), 'import', lambda _: '/app/admin/bulk-import')
    recent(select(Verification.id, Organization.name, Verification.status, Verification.reviewed_at).join(
        Organization, Organization.id == Verification.org_id).where(Verification.reviewed_at.is_not(None)).order_by(
        Verification.reviewed_at.desc(), Verification.id), 'verification', lambda _: '/app/admin/verification')
    return dict(updated_at=datetime.now(UTC).isoformat(), days=days, timezone='UTC', kpis=kpis,
                series=list(series.values()), statuses=[dict(status=s, count=n) for s,n in sorted(statuses.items())],
                matching=matching, alerts=alerts, activity=sorted(activity, key=lambda x:(x['timestamp'],x['id']), reverse=True)[:15])


class DashboardDays(IntEnum):
    week = 7
    month = 30
    quarter = 90


class DashboardSeries(BaseModel):
    date: str
    rfqs_created: int
    rfqs_published: int
    suppliers_created: int
    suppliers_verified: int


class DashboardStatus(BaseModel):
    status: str
    count: int


class DashboardAlert(BaseModel):
    key: str
    count: int
    href: str


class DashboardActivity(BaseModel):
    id: str
    kind: str
    label: str
    status: str
    timestamp: datetime
    href: str


class DashboardAnalytics(BaseModel):
    updated_at: datetime
    days: int
    timezone: str
    kpis: dict[str, int]
    series: list[DashboardSeries]
    statuses: list[DashboardStatus]
    matching: dict[str, int]
    alerts: list[DashboardAlert]
    activity: list[DashboardActivity]


@router.get('/dashboard/analytics', response_model=DashboardAnalytics)
@cached_get('admin-dashboard-analytics', 30, admin_only=True)
def analytics(response: Response, days: DashboardDays = Query(DashboardDays.month),
              admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    return dashboard_analytics(db, days)
