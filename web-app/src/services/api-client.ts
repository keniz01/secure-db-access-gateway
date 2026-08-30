import axios from 'axios';
import { API_BASE_URL } from '../configs/url-config';
import authService from './auth-service';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true  // Automatically send httpOnly cookies
});

// Add security headers
apiClient.interceptors.request.use((config) => {
  config.headers['X-Requested-With'] = 'XMLHttpRequest';

  const user = authService.getUser();
  if (user) {
    config.headers['X-User-Role'] = user.role || 'viewer';
    config.headers['X-User-Email'] = user.email;
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