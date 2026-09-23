import type { QueryClient } from "@tanstack/react-query";
import { API_BASE_URL } from "../configs/url-config";
import authService from "./auth-service";
import apiClient from "./api-client";

const AuthApi = {
  login: () => {
    // OAuth 2.1: static OAUTH_REDIRECT_URI (server ignores redirect_origin)
    window.location.href = `${API_BASE_URL}/api/login`;
  },
  
  logout: async (queryClient: QueryClient) => {
    try {
      const res = await apiClient.post("/api/logout");
      const logoutUrl = (res.data as { logout_url?: string })?.logout_url;
      authService.removeToken();
      authService.removeUser();
      queryClient.clear();
      if (logoutUrl) {
        window.location.href = logoutUrl;
        return;
      }
    } catch {
      // best-effort; fall through to local clear
    }
    authService.removeToken();
    authService.removeUser();
    queryClient.clear();
    window.location.href = "/login";
  },
};

export { AuthApi as authApi };