import { createContext, type Dispatch, type SetStateAction } from "react";
import type { User } from "../models/user-profile";

interface AuthContextType {
  user: User | null;
  setUser: Dispatch<SetStateAction<User | null>>;
  isLoading: boolean;
  logout: () => void;
}

// We initialize with undefined so the hook can throw a helpful error if used outside a provider
export const AuthContext = createContext<AuthContextType | undefined>(undefined);