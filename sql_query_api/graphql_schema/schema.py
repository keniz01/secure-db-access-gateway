import strawberry
from routes.music_query_controller import Query

schema = strawberry.Schema(query=Query)
