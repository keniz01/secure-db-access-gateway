# QA Setup Reference

| Component | Technology | Setup Command | Run Command |
|---|---|---|---|
| **web-app** | Node/npm | `npm install -D vitest @testing-library/react @testing-library/user-event jsdom msw` | `npm test` |
| **APIs** | Python/uv | `uv add --dev pytest pytest-asyncio httpx pytest-mock coverage` | `pytest` |
