export type UserRole = 'viewer' | 'admin';

export interface User {
  id: string;
  email: string;
  name: string;
  org_id?: string;
  role?: UserRole;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: User;
} 

export interface DashboardResponse {
  email: string;
  name: string;
  org_id?: string;
  message: string;
}