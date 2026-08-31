import type { User, UserRole } from "../models/user-profile";

const ADMIN_EMAILS = (import.meta.env.VITE_ADMIN_EMAILS || '')
  .split(',')
  .map((email: string) => email.trim().toLowerCase())
  .filter(Boolean);

const inferRole = (user: Pick<User, 'email'> | null): UserRole => {
  if (!user?.email) return 'viewer';
  return ADMIN_EMAILS.includes(user.email.toLowerCase()) ? 'admin' : 'viewer';
};

// Security Note: Tokens are stored via httpOnly cookies (set by backend)
// LocalStorage is used only for non-sensitive user metadata
const authService = {
  getToken: () => {
    // Token is automatically sent via httpOnly cookie by the backend
    // This method checks if user is authenticated
    return localStorage.getItem('app_jwt_exists') ? 'exists' : null;
  },
  setToken: (token: string) => {
    // Store only a flag indicating token exists
    // Actual token should be set via httpOnly cookie by backend
    localStorage.setItem('app_jwt_exists', token ? 'true' : '');
  },
  removeToken: () => localStorage.removeItem('app_jwt_exists'),
  getUser: () => {
    const userStr = localStorage.getItem('user');
    try {
      const user = userStr ? JSON.parse(userStr) : null;
      if (!user) return null;
      return { ...user, role: inferRole(user) };
    } catch {
      // Invalid JSON, remove corrupted data
      localStorage.removeItem('user');
      return null;
    }
  },
  setUser: (user: User) => {
    if (user && user.email && user.name && user.id) {
      localStorage.setItem('user', JSON.stringify({ ...user, role: inferRole(user) }));
    }
  },
  removeUser: () => localStorage.removeItem('user'),
  isAuthenticated: () => !!authService.getToken(),
};

export default authService;