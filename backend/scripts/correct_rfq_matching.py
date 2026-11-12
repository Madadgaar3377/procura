"""Preview (rollback by default) a scoped taxonomy correction and RFQ rerun.

Use --apply only after deploying the matcher and notification worker changes.
Does not send notifications, delete invitations, create quotes, or run other jobs.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, func
from app.database import SessionLocal
from app.models import Category, Invitation, Material, MaterialRevision, Organization, RFQ, RFQMatch
from app.modules.materials.service import material_snapshot, normalize_text
from app.modules.matching.service import match_and_invite
from app.security import audit


def reclassify(db, material_id, subcategory_id):
    material = db.scalar(select(Material).where(Material.id == material_id).with_for_update())
    sub = db.get(Category, subcategory_id)
    if not material or not sub or not sub.active or not sub.parent_id:
        raise ValueError('An existing active material and specific subcategory are required')
    if normalize_text(material.name_en) != normalize_text(sub.name_en):
        raise ValueError('This repair only accepts an exact taxonomy label, never descriptive or fuzzy equivalence')
    if material.main_category_id != sub.parent_id:
        raise ValueError('Material and subcategory must already share the same main category')
    if material.subcategory_id == sub.id and material.category_id == sub.id:
        return False
    if material.subcategory_id is not None:
        raise ValueError('Material already has a different subcategory; explicit administrative review is required')
    before = material_snapshot(material)
    material.category_id = sub.id
    material.subcategory_id = sub.id
    material.version += 1
    db.add(MaterialRevision(material_id=material.id, version=material.version, action='taxonomy_correction',
                            snapshot=material_snapshot(material)))
    audit(db, None, 'material.rfq_taxonomy_correction', entity_id=material.id,
          previous=before, corrected=material_snapshot(material), reason='Explicit RFQ matching incident repair')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rfq', required=True)
    parser.add_argument('--repair-material')
    parser.add_argument('--subcategory')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if bool(args.repair_material) != bool(args.subcategory):
        parser.error('--repair-material and --subcategory must be supplied together')
    with SessionLocal() as db:
        rfq = db.scalar(select(RFQ).where(RFQ.id == args.rfq).with_for_update())
        if not rfq:
            raise ValueError('RFQ not found')
        before_unrelated = db.scalar(select(func.count()).select_from(Invitation).where(Invitation.rfq_id != rfq.id))
        corrected = reclassify(db, args.repair_material, args.subcategory) if args.repair_material else False
        db.flush()
        results = match_and_invite(db, rfq)
        db.flush()
        repeated = match_and_invite(db, rfq)
        db.flush()
        assert repeated['new_invitations'] == 0, 'Rerun generated duplicate invitations'
        assert before_unrelated == db.scalar(select(func.count()).select_from(Invitation).where(Invitation.rfq_id != rfq.id))
        rows = db.execute(select(Organization.name, RFQMatch.status, RFQMatch.reasons, Invitation.status.label('invitation_status'))
            .join(RFQMatch, RFQMatch.supplier_org_id == Organization.id)
            .outerjoin(Invitation, (Invitation.rfq_id == rfq.id) & (Invitation.supplier_org_id == Organization.id))
            .where(RFQMatch.rfq_id == rfq.id).order_by(Organization.name)).mappings().all()
        audit(db, None, 'rfq.match_incident_rerun', rfq.buyer_org_id, rfq.id,
              new_invitations=results['new_invitations'], taxonomy_corrected=corrected,
              historical_invitations_preserved=True)
        print(json.dumps({'mode': 'apply' if args.apply else 'rollback_preview', 'reference': rfq.reference,
                          'taxonomy_corrected': corrected,
                          'summary': {k: v for k, v in results.items() if k != 'invitations'},
                          'rerun_new_invitations': repeated['new_invitations'],
                          'unrelated_invitations_created': 0, 'matches': [dict(r) for r in rows]}, default=str))
        if args.apply:
            db.commit()
            print('Committed scoped correction; historical invitations preserved. No delivery worker was run.')
        else:
            db.rollback()
            print('Rolled back preview; no database changes retained.')


if __name__ == '__main__':
    main()
