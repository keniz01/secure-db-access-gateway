import type { QueryClient } from "@tanstack/react-query";
import { API_BASE_URL } from "../configs/url-config";
import type { DashboardResponse } from "../models/user-profile";
import apiClient from "./api-client";
import authService from "./auth-service";

const AuthApi = {
  login: () => {
    const origin = window.location.origin;
    const redirectUrl = `${API_BASE_URL}/api/login?redirect_origin=${encodeURIComponent(origin)}`;
    window.location.href = redirectUrl;
  },
  
  logout: (queryClient: QueryClient) => {
    authService.removeToken();
    authService.removeUser();
    queryClient.clear();
    window.location.href = `${API_BASE_URL}/api/logout`;
  },
  
  fetchDashboard: async (): Promise<DashboardResponse> => {
    try {
      const { data } = await apiClient.get<DashboardResponse>('/api/dashboard');
      console.log('Dashboard data fetched:', data);
      return data;
    } catch (error) {
      const axiosError = error as { response?: { data?: { detail?: string } } };
      const message = axiosError.response?.data?.detail || (error as Error).message || 'Failed to fetch dashboard data';
      throw new Error(message);
    }
  },
};

export { AuthApi as authApi };