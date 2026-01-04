import axios from 'axios';
import authService from '../services/auth-service';
import { API_BASE_URL } from '../configs/url-config';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true
});

apiClient.interceptors.request.use((config) => {
  const token = authService.getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
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