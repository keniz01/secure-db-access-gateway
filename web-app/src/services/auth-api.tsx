import type { QueryClient } from "@tanstack/react-query";
import { API_BASE_URL } from "../configs/url-config";
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
};

export { AuthApi as authApi };