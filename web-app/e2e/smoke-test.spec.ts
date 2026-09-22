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
              executeSqlStatementWithPresentation: {
                rows: [
                  { id: 101, name: 'Abbey Road', artist: 'The Beatles', release_year: 1969 },
                  { id: 102, name: 'Kind of Blue', artist: 'Miles Davis', release_year: 1959 },
                ],
                presentation: {
                  format: 'table',
                  content: null,
                  reason: 'multi-column relational data',
                  columnLabels: { release_year: 'Release Year' },
                },
              },
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

    // Verify the new layout renders (header with Data Gateway title)
    await expect(page.getByText('Data Gateway')).toBeVisible();

    // Verify security banner is visible
    await expect(page.getByText('Read-only')).toBeVisible();

    // Verify SQL mode is active (default)
    await expect(page.getByRole('button', { name: /SQL/i })).toBeVisible();

    // Type SQL query into Monaco editor
    const monacoEditor = page.locator('.monaco-editor').first();
    await expect(monacoEditor).toBeVisible();
    await monacoEditor.click();
    await page.keyboard.type('SELECT id, name, artist, release_year FROM album');

    // Click Run query button
    const runButton = page.getByRole('button', { name: /Run query/i });
    await expect(runButton).toBeVisible();
    await runButton.click();

    // Verify query results render in the results table
    await expect(page.getByText('Abbey Road')).toBeVisible();
    await expect(page.getByText('The Beatles')).toBeVisible();
    await expect(page.getByText('Kind of Blue')).toBeVisible();
    await expect(page.getByText('Miles Davis')).toBeVisible();
    await expect(page.getByText('1969')).toBeVisible();

    // Column headers use LLM labels with a deterministic fallback for the rest
    await expect(page.getByRole('columnheader', { name: 'Release Year' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'ID' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Artist' })).toBeVisible();
  });
});
