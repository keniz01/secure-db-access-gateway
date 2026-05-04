# SQL Security & GraphQL (sql_query_api)

### SQL Safety (Critical)
```python
@pytest.mark.parametrize("query", ["DROP TABLE", "UPDATE tracks", "DELETE FROM"])
def test_blocks_malicious(checker, query):
    with pytest.raises(ForbiddenSQLStatementException):
        checker.check(query)
```

### GraphQL (Strawberry)
```python
async def test_graphql(async_client):
    query = "{ musicQueries { execute(sql: \"SELECT 1\") { rows } } }"
    resp = await async_client.post("/graphql", json={"query": query})
    assert resp.status_code == 200
```
