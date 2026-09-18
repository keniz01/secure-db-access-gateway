import axios from 'axios';
import { API_BASE_URL } from '../configs/url-config';
import authService from './auth-service';

const CSRF_COOKIE = 'csrf_token';

function readCookie(name: string): string | null {
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}=([^;]*)`)
  );
  return match ? decodeURIComponent(match[1]) : null;
}

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true  // Automatically send httpOnly cookies
});

function generateCorrelationId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// Add security headers. Identity and authorization are derived server-side from
// validated Auth0 claims; caller-supplied X-User-* headers are never trusted.
// The double-submit CSRF token (set by the backend as a non-HttpOnly cookie on
// login) is echoed back as X-CSRF-Token on every request; the server rejects
// state-changing calls without a cookie==header==session token match.
apiClient.interceptors.request.use((config) => {
  config.headers['X-Requested-With'] = 'XMLHttpRequest';
  const correlationId = generateCorrelationId();
  config.headers['X-Correlation-ID'] = correlationId;
  config.headers['X-Request-ID'] = correlationId;
  const csrfToken = readCookie(CSRF_COOKIE);
  if (csrfToken) {
    config.headers['X-CSRF-Token'] = csrfToken;
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      authService.removeToken();
      authService.removeUser();
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

export default apiClient;