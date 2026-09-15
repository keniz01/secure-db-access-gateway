import strawberry

from routes.sql_query_controller import Query, build_graphql_extensions


def make_schema() -> strawberry.Schema:
    """
    Build the Strawberry GraphQL schema.

    The schema is constructed fresh on each call so that environment-dependent
    extensions (e.g. ``DisableIntrospection``) reflect the current runtime,
    not the first import.
    """
    return strawberry.Schema(query=Query, extensions=build_graphql_extensions())
