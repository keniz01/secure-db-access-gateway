import type { User } from "../models/user-profile";

const authService = {
  getToken: () => localStorage.getItem('app_jwt'),
  setToken: (token: string) => localStorage.setItem('app_jwt', token),
  removeToken: () => localStorage.removeItem('app_jwt'),
  getUser: () => {
    const userStr = localStorage.getItem('user');
    return userStr ? JSON.parse(userStr) : null;
  },
  setUser: (user: User) => localStorage.setItem('user', JSON.stringify(user)),
  removeUser: () => localStorage.removeItem('user'),
  isAuthenticated: () => !!authService.getToken(),
};

export default authService;