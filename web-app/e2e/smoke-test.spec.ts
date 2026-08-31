import { test, expect } from '@playwright/test';

test.describe('E2E Smoke Test: Login -> Run Query -> View Results', () => {
  test('unauthenticated user is redirected to login page', async ({ page }) => {
    // Intercept user auth check returning 401 Unauthenticated
    await page.route('**/api/user', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Not authenticated' }),
      });
    });

    await page.goto('/');
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole('heading', { name: 'Welcome Back' })).toBeVisible();
    await expect(page.getByRole('button', { name: /Login with Auth0/i })).toBeVisible();
  });

  test('authenticated user can execute SQL query and view results table', async ({ page }) => {
    // Mock user auth API endpoint
    await page.route('**/api/user', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'auth0|smoke-test-user-123',
          email: 'smoke.tester@example.com',
          name: 'Smoke Tester',
        }),
      });
    });

    // Mock dashboard message API endpoint
    await page.route('**/api/dashboard', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          email: 'smoke.tester@example.com',
          name: 'Smoke Tester',
          message: 'Welcome back, Smoke Tester!',
        }),
      });
    });

    // Mock GraphQL query execution endpoint
    await page.route('**/graphql', async (route) => {
      const request = route.request();
      const postData = request.postDataJSON();

      if (postData?.query?.includes('executeSqlStatement')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            data: {
              executeSqlStatement: [
                { id: 101, name: 'Abbey Road', artist: 'The Beatles', release_year: 1969 },
                { id: 102, name: 'Kind of Blue', artist: 'Miles Davis', release_year: 1959 },
              ],
            },
          }),
        });
      } else {
        await route.continue();
      }
    });

    // Set localStorage app_jwt_exists flag and user metadata
    await page.addInitScript(() => {
      window.localStorage.setItem('app_jwt_exists', 'true');
      window.localStorage.setItem(
        'user',
        JSON.stringify({
          id: 'auth0|smoke-test-user-123',
          email: 'smoke.tester@example.com',
          name: 'Smoke Tester',
        })
      );
    });

    // Navigate to root Dashboard
    await page.goto('/');

    // Verify Dashboard renders user info
    await expect(page.getByText('Welcome, Smoke Tester!')).toBeVisible();
    await expect(page.getByText('smoke.tester@example.com')).toBeVisible();

    // Type SQL query into input area
    const queryInput = page.getByPlaceholder(/Enter your SQL query/i);
    await expect(queryInput).toBeVisible();
    await queryInput.fill('SELECT id, name, artist, release_year FROM album');

    // Click Execute Query button
    const executeButton = page.getByRole('button', { name: /Execute Query/i });
    await expect(executeButton).toBeVisible();
    await executeButton.click();

    // Verify query results render in the results table
    await expect(page.getByText('Abbey Road')).toBeVisible();
    await expect(page.getByText('The Beatles')).toBeVisible();
    await expect(page.getByText('Kind of Blue')).toBeVisible();
    await expect(page.getByText('Miles Davis')).toBeVisible();
    await expect(page.getByText('1969')).toBeVisible();
  });
});
