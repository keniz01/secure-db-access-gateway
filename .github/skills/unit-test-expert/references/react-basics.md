# React Basics Testing (web-app)

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import MyComponent from './MyComponent'

it('renders and interacts', async () => {
  const user = userEvent.setup()
  render(<MyComponent label="Hello" />)
  expect(screen.getByText(/hello/i)).toBeInTheDocument()
  await user.click(screen.getByRole('button'))
})

it('tests hooks', () => {
  const { result } = renderHook(() => useMyHook())
  expect(result.current.value).toBe(true)
})
```
