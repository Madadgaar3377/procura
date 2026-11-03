"""Bounded literal search and stable pagination for existing list contracts."""
from fastapi import Query
from sqlalchemy import or_


class ListSearch:
    def __init__(self,q:str=Query('',max_length=200),limit:int=Query(100,ge=1,le=100),offset:int=Query(0,ge=0),org_id:str|None=None):
        self.q=q.strip()
        self.limit=limit
        self.offset=offset
        self.org_id=org_id

    def apply(self,statement,*columns,order):
        if self.q:
            # Treat wildcard characters as text rather than unexpectedly broad queries.
            escaped=self.q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            pattern=('%' if len(self.q)>=3 else '')+escaped+'%'
            statement=statement.where(or_(*(column.ilike(pattern,escape='\\') for column in columns)))
        return statement.order_by(*order).limit(self.limit).offset(self.offset)
