import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';

/** 统一响应包装（与后端 app/core/response.py 对齐） */
export interface ApiEnvelope<T> {
  code: number;
  message: string;
  data: T;
  trace_id: string;
}

export interface PagedData<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export const http = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
});

/** 令牌存储：access 存 sessionStorage，refresh 存 localStorage */
export const tokenStore = {
  get access() {
    return sessionStorage.getItem('jg_access_token') || '';
  },
  set access(v: string) {
    v ? sessionStorage.setItem('jg_access_token', v) : sessionStorage.removeItem('jg_access_token');
  },
  get refresh() {
    return localStorage.getItem('jg_refresh_token') || '';
  },
  set refresh(v: string) {
    v ? localStorage.setItem('jg_refresh_token', v) : localStorage.removeItem('jg_refresh_token');
  },
  clear() {
    sessionStorage.removeItem('jg_access_token');
    localStorage.removeItem('jg_refresh_token');
  },
};

http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = tokenStore.access;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ── 无感刷新：并发 401 只刷新一次，其余排队重放 ──────────────────
let refreshing: Promise<string> | null = null;

async function doRefresh(): Promise<string> {
  const { data } = await axios.post<ApiEnvelope<{ access_token: string }>>(
    '/api/v1/auth/refresh',
    { refresh_token: tokenStore.refresh },
  );
  if (data.code !== 0) throw new Error(data.message);
  tokenStore.access = data.data.access_token;
  return data.data.access_token;
}

export function refreshAccessToken(): Promise<string> {
  if (!refreshing) {
    refreshing = doRefresh().finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}

/** 刷新失败 → 清空令牌并跳转登录，由调用方决定是否提示 */
export function onSessionExpired() {
  tokenStore.clear();
  if (!location.pathname.startsWith('/login')) {
    location.href = `/login?redirect=${encodeURIComponent(location.pathname)}`;
  }
}

http.interceptors.response.use(
  (resp) => resp,
  async (error: AxiosError<ApiEnvelope<unknown>>) => {
    const status = error.response?.status;
    const original = error.config as (InternalAxiosRequestConfig & { _retry?: boolean }) | undefined;

    if (status === 401 && original && !original._retry && tokenStore.refresh) {
      original._retry = true;
      try {
        const token = await refreshAccessToken();
        original.headers.Authorization = `Bearer ${token}`;
        return http.request(original);
      } catch {
        onSessionExpired();
      }
    }

    const message =
      error.response?.data?.message ?? (error.message || '网络异常，请稍后重试');
    return Promise.reject(new Error(message));
  },
);

export async function unwrap<T>(promise: Promise<{ data: ApiEnvelope<T> }>): Promise<T> {
  const { data } = await promise;
  if (data.code !== 0) throw new Error(data.message);
  return data.data;
}
