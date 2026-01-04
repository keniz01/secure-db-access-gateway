export interface User {
  id: string;
  email: string;
  name: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: User;
} 

export interface DashboardResponse {
  email: string;
  name: string;
  message: string;
}