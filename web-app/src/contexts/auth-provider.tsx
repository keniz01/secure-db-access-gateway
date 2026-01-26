import React, { useState, type ReactNode } from 'react';
import { AuthContext } from './auth-context';
import authService from '../services/auth-service';
import type { User } from '../models/user-profile';
import { useQueryClient } from '@tanstack/react-query';

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(() => authService.getUser());
  const queryClient = useQueryClient();

  const logout = () => {
    authService.removeToken();
    authService.removeUser();
    setUser(null);
    // Clear the TanStack Query cache to prevent data leaking between users
    queryClient.clear();
    window.location.href = '/login';
  };

  const value = {
    user,
    setUser,
    isLoading: false,
    logout
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};