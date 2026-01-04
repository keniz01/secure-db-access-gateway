import type { QueryClient } from "@tanstack/react-query";
import { API_BASE_URL } from "../configs/url-config";
import type { DashboardResponse } from "../models/user-profile";
import apiClient from "./api-client";
import authService from "./auth-service";

const AuthApi = {
  login: () => {
    try {
      const origin = window.location.origin;
      const redirectUrl = `${API_BASE_URL}/api/login?redirect_origin=${encodeURIComponent(origin)}`;
      window.location.href = redirectUrl;
    } catch (error) {
      throw new Error('Failed to initiate login. Please check your connection.');
    }
  },
  
  logout: (queryClient: QueryClient) => {
    authService.removeToken();
    authService.removeUser();
    queryClient.clear();
    // Redirect to Auth0 logout endpoint if available, otherwise just redirect to login
    window.location.href = `${API_BASE_URL}/api/logout` || '/login';
  },
  
  fetchDashboard: async (): Promise<DashboardResponse> => {
    try {
      const { data } = await apiClient.get<DashboardResponse>('/api/dashboard');
      console.log('Dashboard data fetched:', data);
      return data;
    } catch (error: any) {
      const message = error.response?.data?.detail || error.message || 'Failed to fetch dashboard data';
      throw new Error(message);
    }
  },
};

export { AuthApi as authApi };