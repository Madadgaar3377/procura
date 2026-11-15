from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import inspect


def record(obj):
    def value(x):
        if isinstance(x,Decimal): return str(x)
        if isinstance(x,(date,datetime)): return x.isoformat()
        return x
    data = {c.key:value(getattr(obj,c.key)) for c in inspect(obj).mapper.column_attrs}
    if 'avatar_file_id' in data:
        data['avatar_url'] = f"/api/v1/files/public/{data['avatar_file_id']}" if data['avatar_file_id'] else None
    if 'logo_file_id' in data:
        data['logo_url'] = f"/api/v1/files/public/{data['logo_file_id']}" if data['logo_file_id'] else None
    return data
