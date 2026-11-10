import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import SessionLocal
from app.models import RFQ
from app.modules.matching.service import match_and_invite

with SessionLocal() as db:
    rfq = db.get(RFQ, '60f7f99b-ae7e-4647-af98-d40a4fd014f2')
    print("RFQ ID:", rfq.id)
    print("RFQ Title:", rfq.title)
    print("RFQ City:", repr(rfq.city))
    
    # Run with current city ('RAS')
    res_ras = match_and_invite(db, rfq)
    print("Result with city 'RAS':", res_ras)
    
    # What if city is 'ABC'?
    rfq.city = 'ABC'
    res_abc = match_and_invite(db, rfq)
    print("Result if city was 'ABC':", res_abc)
    db.rollback() # Don't commit changes yet
