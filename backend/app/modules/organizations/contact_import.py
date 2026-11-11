"""Supplier contact parsing and idempotent, organization-scoped contact upserts."""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.identity import normalize_email, normalize_phone
from app.models import Contact, ContactChannel, ContactImportSource, Organization


EMAIL_RE = re.compile(r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9.-]*[A-Z0-9])?\.[A-Z]{2,}", re.I)


def clean(value) -> str:
    return unicodedata.normalize('NFKC', str(value or '')).strip()


def norm_name(value: str | None) -> str:
    value = unicodedata.normalize('NFKD', clean(value)).casefold()
    value = ''.join(ch for ch in value if not unicodedata.combining(ch))
    return ' '.join(re.sub(r'[^\w]+', ' ', value).split())


def parse_emails(raw: str | None) -> tuple[list[str], bool]:
    source = clean(raw)
    found = [match.group(0).rstrip('.,;:/') for match in EMAIL_RE.finditer(source)]
    valid: list[str] = []
    for value in found:
        try:
            normalized = normalize_email(value.rstrip('.,;:/'))
        except ValueError:
            continue
        if normalized not in valid:
            valid.append(normalized)
    invalid = bool(source and (not found or re.sub(EMAIL_RE, '', source).strip(' \t\r\n,;/|')))
    return valid, invalid


def parse_phones(raw: str | None, country: str | None = 'SA') -> tuple[list[dict], bool]:
    source = clean(raw)
    if not source:
        return [], False
    chunks = re.split(r'\s+(?=(?:T|TEL|TELEPHONE|M|MOBILE|P|PHONE|F|FAX)\s*:)|[;,|\n]+', source, flags=re.I)
    country_region = (country or 'SA').strip().upper()
    if country_region in {'SAUDI ARABIA','KSA','KINGDOM OF SAUDI ARABIA'}:
        country_region = 'SA'
    parsed = []
    needs_review = False
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        label_match = re.match(r'^(T|TEL|TELEPHONE|M|MOBILE|P|PHONE|F|FAX)\s*:\s*', chunk, re.I)
        label = label_match.group(1).upper() if label_match else ''
        kind = 'fax' if label in {'F', 'FAX'} else 'phone'
        phone_label = 'fax' if kind == 'fax' else 'mobile' if label in {'M', 'MOBILE'} else 'office' if label in {'T', 'TEL', 'TELEPHONE'} else 'other'
        body = chunk[label_match.end():] if label_match else chunk
        body = re.sub(r'^tel\s*:\s*', '', body.strip(), flags=re.I)
        # T: base/extension is a common Saudi office format; keep the extension attached.
        extension = None
        ext_match = re.fullmatch(r'(.+?)\s*/\s*(\d{2,5})', body)
        pieces = [body]
        if phone_label == 'office' and ext_match and len(re.sub(r'\D', '', ext_match.group(1))) >= 7:
            pieces = [ext_match.group(1)]
            extension = ext_match.group(2)
        else:
            pieces = re.split(r'\s*/\s*', body)
        for piece in pieces:
            original = piece.strip().strip('()[]')
            if not original:
                continue
            normalized_input = re.sub(r'^[\[(]\s*(\+?\d{1,3})\s*[\])]\s*', r'+\1 ', original)
            compact_digits = re.sub(r'\D', '', normalized_input)
            if not normalized_input.lstrip().startswith('+') and compact_digits.startswith('00966'):
                normalized_input = '+' + compact_digits[2:]
            elif not normalized_input.lstrip().startswith('+') and compact_digits.startswith('966') and len(compact_digits) in (12, 13):
                normalized_input = '+' + compact_digits
            try:
                normalized = normalize_phone(normalized_input, country_region if not normalized_input.lstrip().startswith('+') else None)
            except Exception:
                # Keep unparseable originals for review, without making them notification eligible.
                needs_review = True
                continue
            parsed.append({'value': original[:300], 'normalized': normalized, 'kind': kind,
                           'label': phone_label, 'extension': extension if len(pieces) == 1 else None})
    return parsed, needs_review


def _channel_values(db: Session, contact: Contact, kind: str, channels=None) -> list[str]:
    values = list(channels.get((contact.id, kind), [])) if channels is not None else list(db.scalars(select(ContactChannel.normalized_value).where(
        ContactChannel.contact_id == contact.id, ContactChannel.kind == kind)))
    legacy = contact.email if kind == 'email' else contact.phone_normalized if kind == 'phone' else contact.fax
    if legacy:
        try:
            legacy = normalize_email(legacy) if kind == 'email' else normalize_phone(legacy, 'SA') if kind == 'phone' and not legacy.startswith('+') else legacy
        except Exception:
            pass
        values.append(legacy)
    return values


def _find_contact(db: Session, org_id: str, name: str | None, emails: list[str], phones: list[dict]) -> Contact | None:
    contacts = db.scalars(select(Contact).where(Contact.org_id == org_id,Contact.merged_into_contact_id.is_(None),Contact.deleted_at.is_(None)).order_by(Contact.created_at, Contact.id)).all()
    channels = {}
    if contacts:
        for channel in db.scalars(select(ContactChannel).where(ContactChannel.contact_id.in_([item.id for item in contacts]))):
            channels.setdefault((channel.contact_id, channel.kind), []).append(channel.normalized_value)
    normalized_name = norm_name(name)
    # Email is the strongest identity. Contradictory names are retained for review by caller.
    for email in emails:
        for contact in contacts:
            if email in _channel_values(db, contact, 'email', channels):
                if not (normalized_name and contact.name and normalized_name != norm_name(contact.name)):
                    return contact
    if phones:
        for phone in phones:
            for contact in contacts:
                if phone['normalized'] not in _channel_values(db, contact, phone['kind'], channels):
                    continue
                names_conflict=bool(normalized_name and contact.name and normalized_name != norm_name(contact.name))
                if not names_conflict and (phone['label'] == 'mobile' or (normalized_name and normalized_name == norm_name(contact.name))):
                    return contact
    if normalized_name:
        matches = [contact for contact in contacts if normalized_name == norm_name(contact.name)]
        if len(matches) == 1:
            return matches[0]
    return None


def _add_channel(db: Session, contact: Contact, kind: str, value: str, normalized: str,
                 label: str, source: str, source_reference: str | None, extension: str | None = None) -> bool:
    exists = db.scalar(select(ContactChannel.id).where(ContactChannel.contact_id == contact.id,
        ContactChannel.kind == kind, ContactChannel.normalized_value == normalized))
    if exists:
        return False
    db.add(ContactChannel(contact_id=contact.id, org_id=contact.org_id, kind=kind, value=value[:300],
        normalized_value=normalized[:220], label=label[:20], extension=extension,
        is_primary=not bool(db.scalar(select(ContactChannel.id).where(ContactChannel.contact_id == contact.id, ContactChannel.kind == kind))),
        needs_review=False, source_value=source[:500] or None, source_reference=source_reference))
    if kind == 'email' and not contact.email:
        contact.email = normalized[:220]
    elif kind == 'phone' and not contact.phone:
        contact.phone = value[:80]
        contact.phone_normalized = normalized[:16]
    elif kind == 'fax' and not contact.fax:
        contact.fax = value[:80]
    return True


def import_contact_row(db: Session, org_id: str, name_value: str | None, raw_email: str | None,
                       raw_phone: str | None, country: str | None, source_reference: str | None,
                       *, return_ids: bool = False, metrics: dict | None = None):
    """Upsert row contacts; ambiguous name/address alignment remains a review record."""
    raw_name, raw_email, raw_phone = clean(name_value), clean(raw_email), clean(raw_phone)
    if not any((raw_name, raw_email, raw_phone)):
        if metrics is not None:
            metrics.update(created_ids=[], updated_ids=[])
        return (0, False, [], 0) if return_ids else (0, False)
    # Serialize contact identity checks for this supplier across concurrent imports.
    db.scalar(select(Organization.id).where(Organization.id == org_id).with_for_update())
    emails, invalid_email = parse_emails(raw_email)
    phones, invalid_phone = parse_phones(raw_phone, country or 'SA')
    names = [part.strip() for part in re.split(r'\s*[/;\n]+\s*', raw_name) if part.strip()]
    ambiguous = len(names) > 1 and len(emails) > 1 and len(names) != len(emails)
    ambiguous = ambiguous or (len(names) > 1 and len(phones) > 1 and len(emails) not in (0, len(names)))
    assignments: list[tuple[str | None, list[str], list[dict]]]
    if len(names) > 1 and emails and len(names) == len(emails):
        assignments = [(person, [email], phones if len(phones) <= 1 else []) for person, email in zip(names, emails)]
        if len(phones) > 1:
            ambiguous = True
    elif len(names) <= 1:
        assignments = [(names[0] if names else None, emails, phones)]
    else:
        assignments = [(raw_name or None, [], [])]
        ambiguous = True
    made = 0
    contact_ids=[]
    created_ids=set();updated_ids=set()
    for person, person_emails, person_phones in assignments:
        contact = _find_contact(db, org_id, person, person_emails, person_phones)
        if (ambiguous or not person) and len(assignments)==1 and contact is None:
            contact=db.scalar(select(Contact).where(Contact.org_id==org_id,Contact.name.is_(None),Contact.raw_email==(raw_email or None),
                Contact.raw_phone==(raw_phone or None),Contact.needs_review.is_(True),Contact.merged_into_contact_id.is_(None),Contact.deleted_at.is_(None)).limit(1))
        existing_email_names=[]
        if person and person_emails:
            existing_email_names = db.scalars(select(Contact.name).outerjoin(ContactChannel,
                (ContactChannel.contact_id == Contact.id) & (ContactChannel.kind == 'email')).where(
                Contact.org_id == org_id, Contact.merged_into_contact_id.is_(None), Contact.deleted_at.is_(None),
                Contact.name.is_not(None), or_(Contact.email.in_(person_emails),
                    ContactChannel.normalized_value.in_(person_emails)))).all()
        identity_conflict=bool(person and any(norm_name(value)!=norm_name(person) for value in existing_email_names))
        identity_conflict = identity_conflict or bool(contact and person and contact.name and norm_name(contact.name) != norm_name(person))
        if identity_conflict:
            # A shared email is useful evidence, but conflicting person names need human review.
            if contact is not None:
                contact.needs_review = True
        if contact is None:
            contact = Contact(org_id=org_id, name=(None if ambiguous else person)[:150] if person and not ambiguous else None,
                raw_email=raw_email[:500] or None, raw_phone=raw_phone[:300] or None,
                needs_review=bool(ambiguous or invalid_email or invalid_phone or identity_conflict), permission_to_notify=False)
            db.add(contact)
            db.flush()
            made += 1
            created_ids.add(contact.id)
        else:
            changed=False
            if not contact.raw_email and raw_email:contact.raw_email=raw_email[:500];changed=True
            if not contact.raw_phone and raw_phone:contact.raw_phone=raw_phone[:300];changed=True
            if not contact.name and person and not ambiguous:
                contact.name = person[:150];changed=True
            contact.needs_review = contact.needs_review or invalid_email or invalid_phone or ambiguous
            if changed:updated_ids.add(contact.id)
        for email in person_emails:
            if _add_channel(db, contact, 'email', email, email, 'other', raw_email, source_reference) and contact.id not in created_ids:updated_ids.add(contact.id)
        phone_identity_conflict = bool(person and contact.name and norm_name(contact.name) != norm_name(person))
        for phone in ([] if phone_identity_conflict else person_phones):
            if _add_channel(db, contact, phone['kind'], phone['value'], phone['normalized'], phone['label'], raw_phone, source_reference, phone['extension']) and contact.id not in created_ids:updated_ids.add(contact.id)
        if ambiguous:
            contact.needs_review = True
            # Preserve parseable but unassignable channels on a single unnamed review record.
            if len(assignments) == 1:
                for email in emails:
                    if _add_channel(db, contact, 'email', email, email, 'other', raw_email, source_reference) and contact.id not in created_ids:updated_ids.add(contact.id)
                for phone in phones:
                    if _add_channel(db, contact, phone['kind'], phone['value'], phone['normalized'], phone['label'], raw_phone, source_reference, phone['extension']) and contact.id not in created_ids:updated_ids.add(contact.id)
        if source_reference and not db.scalar(select(ContactImportSource.id).where(
                ContactImportSource.contact_id==contact.id,ContactImportSource.source_reference==source_reference)):
            db.add(ContactImportSource(contact_id=contact.id,org_id=org_id,source_reference=source_reference,
                raw_name=raw_name[:500] or None,raw_email=raw_email[:1000] or None,raw_phone=raw_phone[:1000] or None))
        if contact.id not in contact_ids:contact_ids.append(contact.id)
    result=(made,bool(invalid_email or invalid_phone or ambiguous))
    if metrics is not None:
        metrics['created_ids'] = list(created_ids)
        metrics['updated_ids'] = list(updated_ids)
    return (*result,contact_ids,len(updated_ids)) if return_ids else result
