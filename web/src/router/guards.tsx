import { Navigate, useLocation } from 'react-router-dom';
import { Spin } from 'antd';
import { useEffect } from 'react';
import { useAuthStore } from '@/stores/authStore';

/** 登录守卫：未登录跳转 /login，并带上回跳地址 */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { user, ready, loadProfile } = useAuthStore();
  const location = useLocation();

  useEffect(() => {
    if (!ready) void loadProfile();
  }, [ready, loadProfile]);

  if (!ready) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 120 }}>
        <Spin size="large" tip="加载中" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}

/** 权限守卫：无权限渲染 403（后端仍会二次校验，这里只影响展示） */
export function PermissionGuard({
  perm,
  children,
}: {
  perm?: string;
  children: React.ReactNode;
}) {
  const hasPerm = useAuthStore((s) => s.hasPerm);
  if (perm && !hasPerm(perm)) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <h2 style={{ fontSize: 48, margin: 0, color: 'var(--text-hint)' }}>403</h2>
        <p style={{ color: 'var(--text-hint)' }}>抱歉，你没有访问该页面的权限（需要权限：{perm}）</p>
      </div>
    );
  }
  return <>{children}</>;
}
