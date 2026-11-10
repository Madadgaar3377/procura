import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import SessionLocal
from app.models import Capability, Organization, RFQ, Category

with SessionLocal() as db:
    tech = db.get(Organization, '3fb07206-e24c-4368-a34f-3266717a9d88')
    rfq = db.get(RFQ, '60f7f99b-ae7e-4647-af98-d40a4fd014f2')
    print("Tech Org:", tech.name, "City:", tech.city)
    print("RFQ:", rfq.reference, "City:", rfq.city)
    
    # Check Tech capabilities in category 94e75a8e-4f47-434e-958e-6415a60ce9c7
    caps = db.query(Capability).filter(
        Capability.org_id == tech.id,
        Capability.category_id == '94e75a8e-4f47-434e-958e-6415a60ce9c7'
    ).all()
    for c in caps:
        print(f"Cap ID: {c.id}, City: {c.city}, Confirmed: {c.confirmed}, Status: {c.eligibility_status}")
