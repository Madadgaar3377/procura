from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import inspect


def record(obj):
    def value(x):
        if isinstance(x,Decimal): return str(x)
        if isinstance(x,(date,datetime)): return x.isoformat()
        return x
    return {c.key:value(getattr(obj,c.key)) for c in inspect(obj).mapper.column_attrs}
