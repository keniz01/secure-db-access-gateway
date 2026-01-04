import type { DashboardResponse } from "../models/user-profile";
import apiClient from "./api-client";

const DashboardApi = {  
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

export { DashboardApi as dashboardApi };