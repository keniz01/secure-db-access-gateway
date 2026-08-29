import strawberry
from routes.sql_query_controller import Query

schema = strawberry.Schema(query=Query)
