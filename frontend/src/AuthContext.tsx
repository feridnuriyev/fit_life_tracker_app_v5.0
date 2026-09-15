import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { router } from 'expo-router';

const BACKEND = process.env.EXPO_PUBLIC_BACKEND_URL || '';
const API = `${BACKEND}/api`;
const TOKEN_KEY = 'fit_token';

type User = {
  user_id: string;
  email: string;
  name: string;
  picture?: string | null;
};

type AuthContextType = {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name: string) => Promise<void>;
  loginWithGoogleCode: (payload: GoogleCodePayload) => Promise<void>;
  logout: () => Promise<void>;
};

type GoogleCodePayload = {
  code: string;
  code_verifier: string;
  redirect_uri: string;
  client_id: string;
};

const AuthContext = createContext<AuthContextType | null>(null);

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
};

export const apiFetch = async (path: string, opts: RequestInit = {}) => {
  const token = await AsyncStorage.getItem(TOKEN_KEY);
  const headers: any = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API}${path}`, { ...opts, headers });
  if (!res.ok) {
    const text = await res.text();
    try {
      const json = JSON.parse(text);
      throw new Error(json.detail || text);
    } catch {
      throw new Error(text || `HTTP ${res.status}`);
    }
  }
  if (res.status === 204) return null;
  return res.json();
};

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const persistToken = async (t: string | null) => {
    if (t) await AsyncStorage.setItem(TOKEN_KEY, t);
    else await AsyncStorage.removeItem(TOKEN_KEY);
    setToken(t);
  };

  const fetchMe = useCallback(async () => {
    try {
      const me = await apiFetch('/auth/me');
      setUser(me);
      return me;
    } catch {
      await persistToken(null);
      setUser(null);
      return null;
    }
  }, []);

  useEffect(() => {
    (async () => {
      const t = await AsyncStorage.getItem(TOKEN_KEY);
      if (t) {
        setToken(t);
        await fetchMe();
      }
      setLoading(false);
    })();
  }, [fetchMe]);

  const login = async (email: string, password: string) => {
    const res = await fetch(`${API}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Login failed' }));
      throw new Error(err.detail || 'Login failed');
    }
    const data = await res.json();
    await persistToken(data.token);
    setUser(data.user);
    router.replace('/(tabs)');
  };

  const register = async (email: string, password: string, name: string) => {
    const res = await fetch(`${API}/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, name }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Sign up failed' }));
      throw new Error(err.detail || 'Sign up failed');
    }
    const data = await res.json();
    await persistToken(data.token);
    setUser(data.user);
    router.replace('/(tabs)');
  };

  const loginWithGoogleCode = async (payload: GoogleCodePayload) => {
    const res = await fetch(`${API}/auth/google`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Google login failed' }));
      throw new Error(err.detail || 'Google login failed');
    }
    const data = await res.json();
    await persistToken(data.token);
    setUser(data.user);
    router.replace('/(tabs)');
  };

  const logout = async () => {
    try {
      await apiFetch('/auth/logout', { method: 'POST' });
    } catch {}
    await persistToken(null);
    setUser(null);
    router.replace('/login');
  };

  return (
    <AuthContext.Provider
      value={{ user, token, loading, login, register, loginWithGoogleCode, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
};
