from pydantic import BaseModel


class SqlStatementRequest(BaseModel):
    sql_statement: str = ""
