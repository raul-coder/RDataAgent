import { create } from 'zustand';
import { tokenStore } from '@/services/http';
import * as api from '@/services/api';

interface AuthState {
  token: string;
  user: api.LoginUser | null;
  loading: boolean;
  /** 已加载过用户信息（用于路由守卫区分"未登录"与"加载中"） */
  ready: boolean;

  login: (username: string, password: string, captcha?: string, captchaId?: string) => Promise<api.LoginUser>;
  loadProfile: () => Promise<api.LoginUser | null>;
  logout: () => Promise<void>;
  hasPerm: (code?: string) => boolean;
  visibleUnits: (menuId: number) => string[] | null;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  token: tokenStore.access,
  user: null,
  loading: false,
  ready: false,

  async login(username, password, captcha, captchaId) {
    set({ loading: true });
    try {
      const res = await api.login({ username, password, captcha, captcha_id: captchaId });
      tokenStore.access = res.access_token;
      tokenStore.refresh = res.refresh_token;
      set({ token: res.access_token, user: res.user, ready: true });
      return res.user;
    } finally {
      set({ loading: false });
    }
  },

  async loadProfile() {
    if (!tokenStore.access) {
      set({ user: null, ready: true });
      return null;
    }
    set({ loading: true });
    try {
      const user = await api.fetchProfile();
      set({ user, token: tokenStore.access, ready: true });
      return user;
    } catch {
      tokenStore.clear();
      set({ user: null, token: '', ready: true });
      return null;
    } finally {
      set({ loading: false });
    }
  },

  async logout() {
    try {
      await api.logout();
    } catch {
      // 后端已失效也要清理本地
    }
    tokenStore.clear();
    set({ user: null, token: '', ready: true });
  },

  /** 超级管理员或拥有该权限码 */
  hasPerm(code) {
    if (!code) return true;
    const user = get().user;
    if (!user) return false;
    if (user.role_codes?.includes('SUPER_ADMIN')) return true;
    return user.perms?.includes(code) ?? false;
  },

  /** 某菜单的经营单元可见范围；null 表示不限制 */
  visibleUnits(menuId) {
    const user = get().user;
    if (!user) return null;
    const v = user.data_perms?.[String(menuId)];
    return v === undefined || v === null || v.length === 0 ? null : v;
  },
}));
