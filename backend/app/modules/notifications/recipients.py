"""RFQ procurement recipients. Verification is an identity fact, not permission.

An explicit capability assignment or primary designation authorizes that scope.
Legacy permission_to_notify alone never establishes procurement scope.
Destination restrictions always win, including through company fallbacks.
"""
from hashlib import sha256

from sqlalchemy import select

from app.identity import normalize_email, normalize_phone
from app.models import (AccountPreference, Capability, Contact, ContactCapability,
                        ContactChannel, DeliveryRestriction, Member, Organization,
                        Outbox, RFQ, RFQItem, User)


def normalize_destination(channel, value):
    if channel == 'email':
        return normalize_email(value)
    value = value.strip()
    if value.startswith('00966'):
        value = '+' + value[2:]
    elif value.startswith('966'):
        value = '+' + value
    return normalize_phone(value, None if value.startswith('+') else 'SA', sms=True)


def restriction(db, channel, destination):
    row = db.scalar(select(DeliveryRestriction).where(
        DeliveryRestriction.channel == channel, DeliveryRestriction.destination == destination))
    return row.reason if row else None


def resolve(db, org_id, rfq_id, template='rfq_invitation'):
    org = db.get(Organization, org_id)
    from app.modules.matching.service import _match_batch
    rfq = db.get(RFQ, rfq_id)
    evaluated = _match_batch(db, rfq, evaluate_only=True, supplier_ids=[org_id]) if rfq else {}
    covered = set(evaluated.get('covered_items', {}).get(org_id, []))
    items = db.scalars(select(RFQItem).where(RFQItem.rfq_id == rfq_id)).all()
    # Only use items actually supported by this supplier, including partial RFQs.
    items = [item for item in items if item.id in covered]
    contacts = db.scalars(select(Contact).where(Contact.org_id == org_id,
        Contact.deleted_at.is_(None), Contact.merged_into_contact_id.is_(None)).order_by(Contact.id)).all()
    links = db.execute(select(ContactCapability, Capability).join(Capability,
        Capability.id == ContactCapability.capability_id).where(Capability.org_id == org_id,
        Capability.confirmed.is_(True), Capability.accepts_rfqs.is_(True),
        Capability.eligibility_status == 'enabled')).all()
    channels = db.scalars(select(ContactChannel).where(ContactChannel.org_id == org_id)
        .order_by(ContactChannel.is_primary.desc(), ContactChannel.id)).all()
    output = {'email': [], 'sms': []}
    reasons = {'email': [], 'sms': []}
    account_fallbacks = db.execute(select(Member, User).join(User, User.id == Member.user_id).where(
        Member.organization_id == org_id, Member.role.in_({'owner', 'manager', 'supplier'}),
        User.is_active.is_(True)).order_by(Member.id)).all()
    for item in items:
        ranked = []
        for contact in contacts:
            scopes = [(link, cap) for link, cap in links if link.contact_id == contact.id]
            ranks = []
            for link, cap in scopes:
                materials = link.material_ids if link.material_ids is not None else cap.material_ids or []
                if item.material_id and item.material_id in materials:
                    ranks.append((0, not link.is_primary))
                elif not link.material_ids and cap.category_id == item.subcategory_id:
                    ranks.append((1, not link.is_primary))
            if ranks:
                rank, secondary = min(ranks)
                ranked.append((rank, secondary, contact.id, contact))
            elif contact.is_primary and not scopes:
                ranked.append((2, False, contact.id, contact))
        candidates = {'email': [], 'sms': []}
        if any(c.fax for c in contacts) or any(c.kind == 'fax' for c in channels):
            reasons['sms'].append('invalid_contact')
        for rank, _, _, contact in sorted(ranked):
            for channel, kind, legacy in [('email', 'email', contact.email),
                                           ('sms', 'phone', contact.phone_normalized or contact.phone)]:
                values = [c.value for c in channels if c.contact_id == contact.id and c.kind == kind and not c.extension]
                if legacy:
                    values.append(legacy)
                for value in dict.fromkeys(values):
                    candidates[channel].append((value, contact.id, contact.name, ['material', 'subcategory', 'primary'][rank], None))
        # Opted-out account destinations cannot re-enter through another identity.
        account_optouts = {'email': set(), 'sms': set()}
        # Authorized account roles are primary procurement fallbacks, never viewers.
        for member, user in account_fallbacks:
            prefs = db.get(AccountPreference, user.id)
            if prefs and not prefs.events.get(template, True):
                for channel,value in [('email',user.email),('sms',user.phone_e164)]:
                    if value:
                        try:account_optouts[channel].add(normalize_destination(channel,value))
                        except ValueError:pass
                continue
            for channel, value, allowed in [('email', user.email, user.email_notifications_enabled),
                                            ('sms', user.phone_e164, user.sms_notifications_enabled)]:
                if value:
                    if not allowed:
                        try:account_optouts[channel].add(normalize_destination(channel,value))
                        except ValueError:pass
                    candidates[channel].append((value, None, user.username, 'account', user.id if allowed else False))
        for channel, value in [('email', org.business_email), ('sms', org.business_phone)]:
            if value:
                candidates[channel].append((value, None, org.contact_name, 'company', None))
        for channel in output:
            for value, contact_id, name, source, user_id in candidates[channel]:
                try:
                    destination = normalize_destination(channel, value)
                except ValueError:
                    reasons[channel].append('invalid_contact')
                    continue
                blocked = restriction(db, channel, destination)
                if user_id is False or destination in account_optouts[channel]:
                    blocked = 'opted_out'
                if blocked:
                    reasons[channel].append(blocked)
                    continue
                selected = {'destination': destination, 'contact_id': contact_id, 'contact_name': name,
                            'recipient_source': source, 'user_id': user_id}
                if not any(r['destination'] == destination for r in output[channel]):
                    output[channel].append(selected)
                break
    for channel in output:
        if not output[channel]:
            reason = next((r for r in ('opted_out', 'suppressed', 'invalid_contact') if r in reasons[channel]), 'no_contact')
            output[channel] = [{'skip_reason': reason, 'reason_detail':
                ('No phone available' if channel == 'sms' else 'No email available') if reason == 'no_contact'
                else 'No usable mobile number (invalid, landline or fax)' if channel == 'sms' and reason == 'invalid_contact'
                else reason.replace('_', ' ')}]
    return output


def enqueue(db, *, org_id, template, context, source_key):
    context={**context, 'supplier_name':db.get(Organization,org_id).name}
    recipients = resolve(db, org_id, context['rfq_id'], template)
    for channel, rows in recipients.items():
        for recipient in rows:
            suffix = sha256(recipient.get('destination', 'skipped').encode()).hexdigest()
            key = f'{source_key}:{channel}:{suffix}'
            if db.scalar(select(Outbox.id).where(Outbox.dedupe_key == key)):
                continue
            skip = recipient.get('skip_reason')
            db.add(Outbox(event='notification.delivery', dedupe_key=key,
                status='skipped' if skip else 'pending', delivery_status=skip,
                payload={'channel': channel, 'template': template, 'procurement_recipient': True,
                         **recipient, 'context': context}))
    db.flush()


def current_recipient(db, payload):
    context = payload['context']
    recipients = resolve(db, context['org_id'], context['rfq_id'], payload['template'])
    return any(r.get('destination') == payload.get('destination') for r in recipients[payload['channel']])
