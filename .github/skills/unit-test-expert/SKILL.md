---
name: unit-test-expert
description: >
  Expert in creating, updating, and improving unit and integration tests.
  Triggers: "add tests", "update tests", "unit test", "fix test", "test coverage".
  Stack: React 19 (Vitest) and FastAPI (pytest).
---

# Unit Test Expert

Use this to generate or update unit tests with high precision and low token usage.

1.  **Determine Scope**: Is this a new unit test or an update to an existing one?
2.  **Select Service**: `web-app`, `sql_query_api`, or `auth0_api`.
3.  **Load Essentials**: Read `references/setup.md` for dependencies/commands.
4.  **Load Reference**: Read **only** the required snippet from `references/`:
    -   `react-basics.md`: UI components and hooks.
    -   `react-advanced.md`: TanStack Query, Router 7, MSW.
    -   `fastapi-basics.md`: Routes and core logic.
    -   `sql-security.md`: SQL safety and GraphQL.
5.  **Implementation**: Create/update the test file following the loaded patterns.
