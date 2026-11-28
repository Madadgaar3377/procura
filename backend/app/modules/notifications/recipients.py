"""RFQ procurement recipients. Verification is an identity fact, not permission.

An explicit capability assignment, source trade responsibility, or primary designation
authorizes that scope. Legacy permission_to_notify alone never establishes procurement
scope. Destination restrictions always win, including through company fallbacks.
"""
from hashlib import sha256

from sqlalchemy import select

from app.identity import normalize_email, normalize_phone
from app.models import (
    AccountPreference,
    Capability,
    Contact,
    ContactCapability,
    ContactChannel,
    ContactSupplierTrade,
    DeliveryRestriction,
    Member,
    Organization,
    Outbox,
    RFQ,
    RFQItem,
    User,
)


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
    """Active restriction reason (opted_out / suppressed / invalid_recipient) or None.

    Cleared restrictions (an admin corrected and revalidated the address) are history.
    """
    row = db.scalar(select(DeliveryRestriction).where(
        DeliveryRestriction.channel == channel, DeliveryRestriction.destination == destination,
        DeliveryRestriction.cleared_at.is_(None)))
    return row.reason if row else None


def resolve_batch(db, org_ids, rfq_id, template='rfq_invitation') -> dict[str, dict]:
    """Batch-resolve targeted contacts for multiple supplier organizations in O(1) queries."""
    unique_orgs = list(dict.fromkeys(org_ids))
    if not unique_orgs:
        return {}
    from app.models import Invitation
    from app.modules.matching.service import supplier_evaluations

    rfq = db.get(RFQ, rfq_id)
    orgs_by_id = {o.id: o for o in db.scalars(select(Organization).where(Organization.id.in_(unique_orgs)))}
    invs_by_org = {
        inv.supplier_org_id: inv
        for inv in db.scalars(select(Invitation).where(
            Invitation.rfq_id == rfq_id,
            Invitation.supplier_org_id.in_(unique_orgs),
        ))
    }
    legacy_orgs = [oid for oid in unique_orgs if oid not in invs_by_org or invs_by_org[oid].matched_item_ids is None]
    legacy_evals = supplier_evaluations(db, rfq, legacy_orgs) if (legacy_orgs and rfq) else {}

    all_items = db.scalars(
        select(RFQItem).where(RFQItem.rfq_id == rfq_id, RFQItem.is_active.is_(True)).order_by(RFQItem.created_at, RFQItem.id)
    ).all()

    contacts_by_org: dict[str, list[Contact]] = {oid: [] for oid in unique_orgs}
    for contact in db.scalars(select(Contact).where(
        Contact.org_id.in_(unique_orgs),
        Contact.deleted_at.is_(None),
        Contact.merged_into_contact_id.is_(None),
    ).order_by(Contact.id)):
        contacts_by_org.setdefault(contact.org_id, []).append(contact)

    links_by_org: dict[str, list[tuple[ContactCapability, Capability]]] = {oid: [] for oid in unique_orgs}
    for link, cap in db.execute(select(ContactCapability, Capability).join(
        Capability, Capability.id == ContactCapability.capability_id
    ).where(
        Capability.org_id.in_(unique_orgs),
        Capability.confirmed.is_(True),
        Capability.accepts_rfqs.is_(True),
        Capability.eligibility_status == 'enabled',
    )).all():
        links_by_org.setdefault(cap.org_id, []).append((link, cap))

    all_contact_ids = [c.id for cs in contacts_by_org.values() for c in cs]
    trade_ids_by_contact: dict[str, set[str]] = {}
    if all_contact_ids:
        for cid, tid in db.execute(select(
            ContactSupplierTrade.contact_id, ContactSupplierTrade.trade_id
        ).where(ContactSupplierTrade.contact_id.in_(all_contact_ids))).all():
            trade_ids_by_contact.setdefault(cid, set()).add(tid)

    channels_by_org: dict[str, list[ContactChannel]] = {oid: [] for oid in unique_orgs}
    for ch in db.scalars(select(ContactChannel).where(
        ContactChannel.org_id.in_(unique_orgs)
    ).order_by(ContactChannel.is_primary.desc(), ContactChannel.id)):
        channels_by_org.setdefault(ch.org_id, []).append(ch)

    accounts_by_org: dict[str, list[tuple[Member, User]]] = {oid: [] for oid in unique_orgs}
    user_ids: set[str] = set()
    for member, user in db.execute(select(Member, User).join(User, User.id == Member.user_id).where(
        Member.organization_id.in_(unique_orgs),
        Member.role.in_({'owner', 'manager', 'supplier'}),
        User.is_active.is_(True),
    ).order_by(Member.id)).all():
        accounts_by_org.setdefault(member.organization_id, []).append((member, user))
        user_ids.add(user.id)

    prefs_by_user: dict[str, AccountPreference] = {}
    if user_ids:
        for pref in db.scalars(select(AccountPreference).where(AccountPreference.user_id.in_(user_ids))):
            prefs_by_user[pref.user_id] = pref

    # Pre-normalize candidate destinations across all orgs so DeliveryRestriction is checked in 1 query.
    possible_dests: dict[str, set[str]] = {'email': set(), 'sms': set()}
    for oid in unique_orgs:
        org = orgs_by_id.get(oid)
        for c in contacts_by_org.get(oid, []):
            for ch_name, raw in (('email', c.email), ('sms', c.phone_normalized or c.phone)):
                if raw:
                    try:
                        possible_dests[ch_name].add(normalize_destination(ch_name, raw))
                    except ValueError:
                        pass
        for ch_row in channels_by_org.get(oid, []):
            if ch_row.kind in {'email', 'phone'} and not ch_row.extension:
                ch_name = 'email' if ch_row.kind == 'email' else 'sms'
                try:
                    possible_dests[ch_name].add(normalize_destination(ch_name, ch_row.value))
                except ValueError:
                    pass
        for _, u in accounts_by_org.get(oid, []):
            for ch_name, raw in (('email', u.email), ('sms', u.phone_e164)):
                if raw:
                    try:
                        possible_dests[ch_name].add(normalize_destination(ch_name, raw))
                    except ValueError:
                        pass
        if org:
            for ch_name, raw in (('email', org.business_email), ('sms', org.business_phone)):
                if raw:
                    try:
                        possible_dests[ch_name].add(normalize_destination(ch_name, raw))
                    except ValueError:
                        pass

    restrictions_map: dict[tuple[str, str], str] = {}
    all_dests = possible_dests['email'] | possible_dests['sms']
    if all_dests:
        for row in db.scalars(select(DeliveryRestriction).where(
            DeliveryRestriction.destination.in_(all_dests),
            DeliveryRestriction.cleared_at.is_(None),
        )):
            restrictions_map[(row.channel, row.destination)] = row.reason

    results: dict[str, dict] = {}
    for oid in unique_orgs:
        org = orgs_by_id.get(oid)
        inv = invs_by_org.get(oid)
        if inv and inv.matched_item_ids is not None:
            evaluated = {'covered_items': inv.matched_item_ids, 'item_evidence': inv.match_evidence or {}}
        else:
            evaluated = legacy_evals.get(oid, {})
        covered = set(evaluated.get('covered_items', []))
        evidence = evaluated.get('item_evidence', {})
        items = [item for item in all_items if item.id in covered]
        contacts = contacts_by_org.get(oid, [])
        links = links_by_org.get(oid, [])
        channels = channels_by_org.get(oid, [])
        account_fallbacks = accounts_by_org.get(oid, [])
        output: dict[str, list[dict]] = {'email': [], 'sms': []}
        reasons: dict[str, list[str]] = {'email': [], 'sms': []}

        for item in items:
            ranked = []
            matched = evidence.get(item.id, {})
            matched_mats = set(matched.get('material_ids', []))
            matched_cats = set(matched.get('category_ids', []))
            matched_trades = set(matched.get('trade_ids', []))
            for contact in contacts:
                scopes = [(link, cap) for link, cap in links if link.contact_id == contact.id]
                contact_trades = trade_ids_by_contact.get(contact.id, set())
                ranks = []
                if matched_trades and (contact_trades & matched_trades):
                    ranks.append((0, not contact.is_primary))
                for link, cap in scopes:
                    materials = link.material_ids if link.material_ids is not None else cap.material_ids or []
                    if item.material_id and item.material_id in materials:
                        ranks.append((0, not link.is_primary))
                    elif matched_mats & set(materials):
                        ranks.append((0, not link.is_primary))
                    elif cap.category_id in matched_cats and link.material_ids is None:
                        ranks.append((1, not link.is_primary))
                    elif not materials and item.subcategory_id and cap.category_id == item.subcategory_id:
                        ranks.append((1, not link.is_primary))
                    elif not materials and item.main_category_id and cap.category_id == item.main_category_id:
                        ranks.append((2, not link.is_primary))
                if ranks:
                    rank, secondary = min(ranks)
                    ranked.append((rank, secondary, contact.id, contact))
                elif contact.is_primary and not scopes and not contact_trades:
                    ranked.append((3, False, contact.id, contact))

            candidates: dict[str, list] = {'email': [], 'sms': []}
            if any(c.fax for c in contacts) or any(c.kind == 'fax' for c in channels):
                reasons['sms'].append('invalid_contact')
            for rank, _, _, contact in sorted(ranked):
                for channel, kind, legacy in [
                    ('email', 'email', contact.email),
                    ('sms', 'phone', contact.phone_normalized or contact.phone),
                ]:
                    values = [c.value for c in channels if c.contact_id == contact.id and c.kind == kind and not c.extension]
                    if legacy:
                        values.append(legacy)
                    for value in dict.fromkeys(values):
                        candidates[channel].append((
                            value, contact.id, contact.name,
                            ['material', 'subcategory', 'category', 'primary'][rank], None,
                        ))
            account_optouts: dict[str, set[str]] = {'email': set(), 'sms': set()}
            for member, user in account_fallbacks:
                if member.role == 'supplier' and contacts:
                    ranked_destinations = {
                        contact.email.strip().casefold() for _, _, _, contact in ranked
                        if contact.email
                    }
                    if not user.email or user.email.strip().casefold() not in ranked_destinations:
                        continue
                prefs = prefs_by_user.get(user.id)
                if prefs and not prefs.events.get(template, True):
                    for channel, value in [('email', user.email), ('sms', user.phone_e164)]:
                        if value:
                            try:
                                account_optouts[channel].add(normalize_destination(channel, value))
                            except ValueError:
                                pass
                    continue
                for channel, value, allowed in [
                    ('email', user.email, user.email_notifications_enabled),
                    ('sms', user.phone_e164, user.sms_notifications_enabled),
                ]:
                    if value:
                        if not allowed:
                            try:
                                account_optouts[channel].add(normalize_destination(channel, value))
                            except ValueError:
                                pass
                        candidates[channel].append((value, None, user.username, 'account', user.id if allowed else False))
            if org:
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
                    blocked = restrictions_map.get((channel, destination))
                    if user_id is False or destination in account_optouts[channel]:
                        blocked = 'opted_out'
                    if blocked:
                        reasons[channel].append(blocked)
                        continue
                    selected = {
                        'destination': destination, 'contact_id': contact_id, 'contact_name': name,
                        'recipient_source': source, 'user_id': user_id, 'item_ids': [item.id],
                    }
                    existing = next((r for r in output[channel] if r['destination'] == destination), None)
                    if existing:
                        if item.id not in existing['item_ids']:
                            existing['item_ids'].append(item.id)
                    else:
                        output[channel].append(selected)
                    break

        for channel in output:
            if not output[channel]:
                reason = next(
                    (r for r in ('opted_out', 'suppressed', 'invalid_recipient', 'invalid_contact') if r in reasons[channel]),
                    'no_contact',
                )
                output[channel] = [{
                    'skip_reason': reason,
                    'reason_detail': (
                        ('No phone available' if channel == 'sms' else 'No email available') if reason == 'no_contact'
                        else 'No usable mobile number (invalid, landline or fax)' if channel == 'sms' and reason == 'invalid_contact'
                        else 'Email address bounced as invalid; correct it in the supplier record' if reason == 'invalid_recipient'
                        else reason.replace('_', ' ')
                    ),
                }]
        results[oid] = output
    return results


def resolve(db, org_id, rfq_id, template='rfq_invitation'):
    return resolve_batch(db, [org_id], rfq_id, template)[org_id]


def with_whatsapp(recipients):
    """WhatsApp uses the same category-scoped mobile destinations as SMS."""
    from app.modules.procurement.whatsapp import configured
    mobiles = [dict(r) for r in recipients.get('sms', []) if r.get('destination')]
    if not configured():
        return recipients
    return {**recipients, 'whatsapp': mobiles or [{'skip_reason': 'no_contact', 'reason_detail': 'No mobile number for WhatsApp'}]}


def enqueue(db, *, org_id, template, context, source_key):
    org = db.get(Organization, org_id)
    context = {**context, 'supplier_name': org.name if org else ''}
    recipients = resolve(db, org_id, context['rfq_id'], template)
    if template in {'rfq_invitation', 'deadline_reminder'}:
        recipients = with_whatsapp(recipients)
    rfq_items = {
        item.id: item
        for item in db.scalars(select(RFQItem).where(RFQItem.rfq_id == context['rfq_id'], RFQItem.is_active.is_(True)))
    }
    pending_keys = []
    staged = []
    for channel, rows in recipients.items():
        for recipient in rows:
            suffix = sha256(recipient.get('destination', 'skipped').encode()).hexdigest()
            key = f'{source_key}:{channel}:{suffix}'
            pending_keys.append(key)
            staged.append((key, channel, recipient))
    existing_keys = set(
        db.scalars(select(Outbox.dedupe_key).where(Outbox.dedupe_key.in_(pending_keys)))
    ) if pending_keys else set()
    for key, channel, recipient in staged:
        if key in existing_keys:
            continue
        existing_keys.add(key)
        skip = recipient.get('skip_reason')
        relevant = [rfq_items[item_id] for item_id in recipient.get('item_ids', []) if item_id in rfq_items]
        recipient_context = {
            **context,
            'materials': [
                {'description': item.description, 'quantity': str(item.quantity), 'unit': item.unit}
                for item in relevant
            ],
        }
        db.add(Outbox(
            event='notification.delivery',
            dedupe_key=key,
            status='skipped' if skip else 'pending',
            delivery_status=skip,
            payload={
                'channel': channel, 'template': template, 'procurement_recipient': True,
                **recipient, 'context': recipient_context,
            },
        ))
    db.flush()


def current_recipient(db, payload):
    context = payload['context']
    recipients = resolve(db, context['org_id'], context['rfq_id'], payload['template'])
    channel = 'sms' if payload['channel'] == 'whatsapp' else payload['channel']
    return any(r.get('destination') == payload.get('destination') for r in recipients[channel])


def queue_invitations_in_app_batch(db, invitations, rfq, outbox_id_by_inv: dict[str, str] | None = None):
    """Queue targeted in-app invitation alerts for all invited organizations in batch."""
    if not invitations:
        return
    from app.models import Notification
    org_ids = [inv.supplier_org_id for inv in invitations]
    members_rows = db.execute(select(Member, User).join(User, User.id == Member.user_id).where(
        Member.organization_id.in_(org_ids),
        User.is_active.is_(True),
        Member.role.in_({'owner', 'manager', 'supplier'}),
    )).all()
    if not members_rows:
        return

    members_by_org: dict[str, list[tuple[Member, User]]] = {}
    all_user_ids: set[str] = set()
    for member, user in members_rows:
        members_by_org.setdefault(member.organization_id, []).append((member, user))
        all_user_ids.add(user.id)

    orgs_needing_resolve = [
        oid for oid, rows in members_by_org.items()
        if len(rows) > 1 or rows[0][0].role == 'supplier'
    ]
    resolved_by_org = resolve_batch(db, orgs_needing_resolve, rfq.id) if orgs_needing_resolve else {}

    if outbox_id_by_inv is None:
        dedupe_keys = [f'rfq-invitation:{inv.id}' for inv in invitations if inv.supplier_org_id in members_by_org]
        outbox_rows = db.execute(
            select(Outbox.dedupe_key, Outbox.id).where(Outbox.dedupe_key.in_(dedupe_keys))
        ).all() if dedupe_keys else []
        by_key = dict(outbox_rows)
        outbox_id_by_inv = {inv.id: by_key.get(f'rfq-invitation:{inv.id}') for inv in invitations}

    prefs_by_user = {
        p.user_id: p
        for p in db.scalars(select(AccountPreference).where(AccountPreference.user_id.in_(all_user_ids)))
    } if all_user_ids else {}

    event_ids = [eid for eid in outbox_id_by_inv.values() if eid]
    existing_pairs = set(db.execute(
        select(Notification.delivery_outbox_id, Notification.recipient_user_id).where(
            Notification.delivery_outbox_id.in_(event_ids),
            Notification.event == 'rfq.invitation',
        )
    ).all()) if event_ids else set()

    for inv in invitations:
        members = members_by_org.get(inv.supplier_org_id)
        if not members:
            continue
        resolved = resolved_by_org.get(inv.supplier_org_id, {})
        destinations = {r['destination'] for rows in resolved.values() for r in rows if r.get('destination')}
        selected = []
        if destinations:
            for member, user in members:
                for channel, value in [('email', user.email), ('sms', user.phone_e164 or user.phone)]:
                    if not value:
                        continue
                    try:
                        matches = normalize_destination(channel, value) in destinations
                    except ValueError:
                        matches = False
                    if matches:
                        selected.append(user)
                        break
        if not selected:
            selected = [user for member, user in members if member.role in {'owner', 'manager'}]
        event_id = outbox_id_by_inv.get(inv.id)
        for user in selected:
            prefs = prefs_by_user.get(user.id)
            if prefs and (not prefs.in_app_enabled or not prefs.events.get('rfq_invitation', True)):
                continue
            if (event_id, user.id) not in existing_pairs:
                existing_pairs.add((event_id, user.id))
                db.add(Notification(
                    recipient_user_id=user.id,
                    event='rfq.invitation',
                    message=f'New RFQ: {rfq.reference}',
                    status='delivered',
                    channel='in_app',
                    delivery_outbox_id=event_id,
                ))
    db.flush()


def queue_invitation_in_app(db, invitation, rfq):
    queue_invitations_in_app_batch(db, [invitation], rfq)
