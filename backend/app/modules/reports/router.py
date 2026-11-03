"""Organization-scoped operational summaries for portal dashboards."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import extract, func, select, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Invitation, Member, Quotation, RFQ, RFQItem, RFQMatch, Organization, Category, User, Notification, Outbox
from app.response_contracts import ReportSummary
from app.security import current_user, org_member
from app.serializers import record

router = APIRouter(prefix='/reports', tags=['Reports & Analytics'])


def _six_month_window(now: datetime) -> tuple[datetime, list[str]]:
    end = now.astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    months: list[str] = []
    year, month = end.year, end.month
    for _ in range(6):
        months.append(f'{year:04d}-{month:02d}')
        month -= 1
        if month == 0:
            year -= 1
            month = 12
    months.reverse()
    start_year, start_month = map(int, months[0].split('-'))
    start = end.replace(year=start_year, month=start_month)
    return start, months


def _count(db: Session, model, *conditions) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0)


def _grouped_months(db: Session, model, date_column, start: datetime, *conditions) -> dict[str, int]:
    year = extract('year', date_column)
    month = extract('month', date_column)
    rows = db.execute(
        select(year, month, func.count())
        .select_from(model)
        .where(date_column.is_not(None), date_column >= start, *conditions)
        .group_by(year, month)
    ).all()
    return {f'{int(row[0]):04d}-{int(row[1]):02d}': int(row[2]) for row in rows}


@router.get('/summary', response_model=ReportSummary)
def summary(
    workspace: Literal['buyer', 'supplier', 'admin'] = Query('buyer'),
    org_id: str | None = Query(None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if workspace == 'admin' and not user.is_admin:
        raise HTTPException(403, 'Administrator access required')
    if workspace != 'admin' and user.is_admin:
        # Admins use the explicit global workspace, keeping the reporting scope visible.
        raise HTTPException(403, 'Choose the administrator workspace for platform-wide reports')

    if workspace == 'admin':
        if org_id is not None:
            raise HTTPException(422, 'Organization filters are not supported for global reports')
        rfq_scope = ()
        invitation_scope = ()
        buyer_rfq_ids = select(RFQ.id)
        supplier_invitation_ids = select(Invitation.id)
        quotation_scope = ()
        invitation_rfq_scope = ()
    elif workspace == 'buyer':
        roles = {'owner', 'manager', 'buyer'}
        if org_id is not None:
            org_member(db, user, org_id, roles)
        org_ids = select(Member.organization_id).join(Organization,Organization.id==Member.organization_id).where(
            Member.user_id == user.id,
            Member.role.in_(roles),
            Organization.is_active.is_(True),
        )
        if org_id is not None:
            org_ids = org_ids.where(Member.organization_id == org_id)
        rfq_scope = (RFQ.buyer_org_id.in_(org_ids),)
        buyer_rfq_ids = select(RFQ.id).where(*rfq_scope)
        invitation_scope = (Invitation.rfq_id.in_(buyer_rfq_ids),)
        invitation_rfq_scope = invitation_scope
        supplier_invitation_ids = select(Invitation.id).where(*invitation_scope)
        quotation_scope = (
            Quotation.rfq_id.in_(buyer_rfq_ids),
            Quotation.status.in_({'submitted','shortlisted','awarded','rejected'}),
        )
    else:
        roles = {'owner', 'manager', 'supplier'}
        if org_id is not None:
            org_member(db, user, org_id, roles)
        org_ids = select(Member.organization_id).join(Organization,Organization.id==Member.organization_id).where(
            Member.user_id == user.id,
            Member.role.in_(roles),
            Organization.is_active.is_(True),
        )
        if org_id is not None:
            org_ids = org_ids.where(Member.organization_id == org_id)
        invitation_scope = (Invitation.supplier_org_id.in_(org_ids),)
        supplier_invitation_ids = select(Invitation.id).where(*invitation_scope)
        buyer_rfq_ids = select(Invitation.rfq_id).where(*invitation_scope).distinct()
        rfq_scope = (RFQ.id.in_(buyer_rfq_ids),)
        invitation_rfq_scope = invitation_scope
        quotation_scope = (Quotation.supplier_org_id.in_(org_ids),)

    def statuses(model,*scope):
        return {status:int(total) for status,total in db.execute(select(model.status,func.count()).where(*scope).group_by(model.status)).all()}
    rfq_statuses=statuses(RFQ,*rfq_scope)
    invitation_statuses=statuses(Invitation,*invitation_scope)
    quotation_statuses=statuses(Quotation,*quotation_scope)
    rfqs_created=sum(rfq_statuses.values())
    invitations_received=sum(invitation_statuses.values())
    invitations_accepted=invitation_statuses.get('accepted',0)
    quotations_submitted=(sum(quotation_statuses.get(status,0) for status in ('submitted','shortlisted','awarded','rejected'))
                          if workspace=='buyer' else quotation_statuses.get('submitted',0))
    quotation_drafts=0 if workspace=='buyer' else quotation_statuses.get('draft',0)
    history_rfq_scope=rfq_scope
    history_invitation_scope=invitation_rfq_scope
    history_quote_scope=(Quotation.status.in_({'submitted','shortlisted','awarded','rejected'}),*quotation_scope)
    rfq_history_model,rfq_date=RFQ,RFQ.created_at
    recent_rfqs=db.scalars(select(RFQ).where(*rfq_scope).order_by(RFQ.created_at.desc(),RFQ.id.desc()).limit(6)).all()
    now=datetime.now(timezone.utc)
    # An abandoned draft with an old deadline is still editable, never a completed RFQ.
    closed_condition=or_(RFQ.status.in_(['closed','cancelled','expired']),
                         (RFQ.status.in_({'published','collecting_quotes','review'})) & (RFQ.response_deadline<=now))
    closed_rfqs=_count(db,RFQ,*rfq_scope,closed_condition)
    active_condition=(RFQ.status.in_({'published','collecting_quotes','review'}),RFQ.response_deadline>now)
    active_rfqs=_count(db,RFQ,*rfq_scope,*active_condition)
    published_rfqs=_count(db,RFQ,*rfq_scope,RFQ.published_at.is_not(None))
    matched_suppliers=_count(db,RFQMatch,RFQMatch.rfq_id.in_(buyer_rfq_ids))
    upcoming=db.scalars(select(RFQ).where(*rfq_scope,*active_condition).order_by(RFQ.response_deadline,RFQ.id).limit(6)).all()
    recent_notifications=[]
    if workspace=='buyer':
        recent_quotations=db.scalars(select(Quotation).where(*quotation_scope).order_by(Quotation.submitted_at.desc(),Quotation.id).limit(6)).all()
        recent_notifications=db.scalars(select(Notification).join(Outbox,Outbox.id==Notification.delivery_outbox_id).where(
            Notification.recipient_user_id==user.id,Notification.channel=='in_app',
            Outbox.payload['rfq_id'].as_string().in_(buyer_rfq_ids),
        ).order_by(Notification.created_at.desc(),Notification.id).limit(6)).all()
    awaiting_response=int(db.scalar(select(func.count()).select_from(Invitation).join(RFQ,RFQ.id==Invitation.rfq_id).where(
        *invitation_scope,Invitation.status.in_({'invited','accepted'}),
        ~select(Quotation.id).where(Quotation.invitation_id==Invitation.id,Quotation.status!='draft').exists(),
        RFQ.response_deadline>now,RFQ.status.in_({'published','collecting_quotes'}))) or 0)
    recent_enquiries=[]
    if workspace!='buyer':recent_quotations=[]
    if workspace=='supplier':
        recent_quotations=db.scalars(select(Quotation).where(*quotation_scope).order_by(Quotation.created_at.desc(),Quotation.id.desc()).limit(6)).all()
        awaiting_response=int(db.scalar(select(func.count()).select_from(Invitation).join(RFQ,RFQ.id==Invitation.rfq_id).where(*invitation_scope,Invitation.status.in_({'invited','accepted'}),~select(Quotation.id).where(Quotation.invitation_id==Invitation.id,Quotation.status!='draft').exists(),RFQ.response_deadline>now,RFQ.status.in_(['published','collecting_quotes']))) or 0)
        enquiries=db.execute(select(Invitation,RFQ,Organization.name,Quotation.status).join(RFQ,RFQ.id==Invitation.rfq_id).join(Organization,Organization.id==RFQ.buyer_org_id).outerjoin(Quotation,Quotation.invitation_id==Invitation.id).where(*invitation_scope).order_by(Invitation.created_at.desc(),Invitation.id).limit(6)).all()
        ids={rfq.id for _,rfq,_,_ in enquiries}
        category_names=dict(db.execute(select(Category.id,Category.name_en)).all()) if ids else {}
        category_pairs={}
        for item in db.scalars(select(RFQItem).where(RFQItem.rfq_id.in_(ids))).all():
            pair=' / '.join(category_names.get(i,'Archived category') for i in [item.main_category_id,item.subcategory_id] if i)
            if pair:category_pairs.setdefault(item.rfq_id,set()).add(pair)
        recent_enquiries=[{'id':inv.id,'rfq_id':rfq.id,'reference':rfq.reference,'buyer':buyer,'project':rfq.title,'categories':sorted(category_pairs.get(rfq.id,set())),'response_deadline':rfq.response_deadline.isoformat(),'invitation_status':inv.status,'quotation_status':quote or 'Not started'} for inv,rfq,buyer,quote in enquiries]

    start, months = _six_month_window(datetime.now(timezone.utc))
    rfq_by_month = _grouped_months(db, rfq_history_model, rfq_date, start, *history_rfq_scope)
    invitation_by_month = _grouped_months(db, Invitation, Invitation.invited_at, start, *history_invitation_scope)
    quote_by_month = _grouped_months(db, Quotation, Quotation.submitted_at, start, *history_quote_scope)
    activity = [
        {
            'month': month,
            'rfqs': rfq_by_month.get(month, 0),
            'invitations': invitation_by_month.get(month, 0),
            'quotations_submitted': quote_by_month.get(month, 0),
        }
        for month in months
    ]

    return {
        'workspace': workspace,
        'rfqs_created': rfqs_created,
        'invitations': invitations_received,
        'invitations_accepted': invitations_accepted,
        'quotations_submitted': quotations_submitted,
        'quotation_drafts': quotation_drafts,
        'activity': activity,
        'rfq_statuses':rfq_statuses,
        'invitation_statuses':invitation_statuses,
        'quotation_statuses':quotation_statuses,
        'recent_rfqs':recent_rfqs,
        'awaiting_response':awaiting_response,
        'closed_rfqs':closed_rfqs,
        'active_rfqs':active_rfqs,
        'published_rfqs':published_rfqs,
        'matched_suppliers':matched_suppliers,
        'upcoming_deadlines':upcoming,
        'recent_notifications':[record(notice) for notice in recent_notifications],
        'recent_enquiries':recent_enquiries,
        'recent_quotations':[record(quote) for quote in recent_quotations],
    }
