# React Advanced Testing (web-app)

### TanStack Query & MSW
```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'

const testQueryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

it('fetches data', async () => {
  server.use(http.get('*/api/user', () => HttpResponse.json({ name: 'User' })))
  render(<QueryClientProvider client={testQueryClient}><UserComponent /></QueryClientProvider>)
  expect(await screen.findByText('User')).toBeInTheDocument()
})
```

### React Router 7
```tsx
import { MemoryRouter } from 'react-router-dom'
render(<MemoryRouter initialEntries={['/home']}><App /></MemoryRouter>)
```
