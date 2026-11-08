"""Shared fail-closed capability eligibility; confirmation remains an audit fact."""
from app.models import Capability


def eligibility_predicates():
    # Buyer matching and public discovery always require an explicit admin
    # approval. The setting remains relevant to automation policy elsewhere.
    return [Capability.eligibility_status=='enabled',Capability.accepts_rfqs.is_(True),
            Capability.confirmed.is_(True)]
