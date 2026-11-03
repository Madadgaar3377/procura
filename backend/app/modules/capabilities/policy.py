"""Shared fail-closed capability eligibility; confirmation remains an audit fact."""
from app.config import get_settings
from app.models import Capability


def eligibility_predicates():
    predicates=[Capability.eligibility_status=='enabled',Capability.accepts_rfqs.is_(True)]
    if get_settings().require_capability_approval:predicates.append(Capability.confirmed.is_(True))
    return predicates
