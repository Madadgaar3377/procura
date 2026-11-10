import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import SessionLocal
from app.models import Capability, Organization, RFQ, RFQMatch
from app.modules.matching.service import match_and_invite

with SessionLocal() as db:
    tech = db.get(Organization, '3fb07206-e24c-4368-a34f-3266717a9d88')
    rfq = db.get(RFQ, '60f7f99b-ae7e-4647-af98-d40a4fd014f2')
    
    # Temporarily add capability for 'RAS'
    ras_cap = Capability(
        org_id=tech.id,
        category_id='94e75a8e-4f47-434e-958e-6415a60ce9c7',
        city='RAS',
        confirmed=True,
        accepts_rfqs=True,
        eligibility_status='enabled'
    )
    db.add(ras_cap)
    db.flush() # flush so SQL query can see it!
    
    # Run matching for RFQ with city 'RAS'
    res_ras = match_and_invite(db, rfq)
    print("With RAS capability & RFQ city 'RAS':", res_ras)
    
    matches = db.query(RFQMatch).filter(RFQMatch.rfq_id == rfq.id).all()
    for m in matches:
        print("Match status:", m.status, "Reasons:", m.reasons)
    
    db.rollback()
