import { Suspense, lazy } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { Spin } from 'antd';
import BasicLayout from '@/layouts/BasicLayout';
import { AuthGuard, PermissionGuard } from './guards';

/**
 * 路由级代码分割（T5-6）：除布局外全部懒加载。
 *
 * 布局不懒加载——它包裹所有受保护页面，必须同步可用；
 * 其余页面（尤其带 ECharts 的驾驶舱、台账、语义层）按需下载，
 * 首屏只需要登录页那部分代码。
 */
const LoginPage = lazy(() => import('@/pages/Login'));
const DashboardPage = lazy(() => import('@/pages/Dashboard'));
const ProfilePage = lazy(() => import('@/pages/Profile'));
const UserManage = lazy(() => import('@/pages/System/UserManage'));
const RoleManage = lazy(() => import('@/pages/System/RoleManage'));
const MenuManage = lazy(() => import('@/pages/System/MenuManage'));
const PermissionConfig = lazy(() => import('@/pages/System/PermissionConfig'));
const OperationLog = lazy(() => import('@/pages/System/OperationLog'));
const AppConfig = lazy(() => import('@/pages/System/AppConfig'));
const ModelConfig = lazy(() => import('@/pages/System/ModelConfig'));
const FeedbackReview = lazy(() => import('@/pages/System/FeedbackReview'));
const AiQaPage = lazy(() => import('@/pages/AiQa'));
const CommercialLedger = lazy(() => import('@/pages/Ledger/CommercialLedger'));
const PplLedger = lazy(() => import('@/pages/Ledger/PplLedger'));
const GoalLedger = lazy(() => import('@/pages/Ledger/GoalLedger'));
const Metrics = lazy(() => import('@/pages/Semantic/Metrics'));
const Dimensions = lazy(() => import('@/pages/Semantic/Dimensions'));
const Rules = lazy(() => import('@/pages/Semantic/Rules'));
const Fewshots = lazy(() => import('@/pages/Semantic/Fewshots'));

const PageLoading = (
  <div style={{ padding: 80, textAlign: 'center' }}>
    <Spin size="large" />
  </div>
);

/**
 * 路由表。
 * perm 与后端菜单的 perm_code 对应：
 *   - 前端按 perm 决定是否渲染（路由守卫）
 *   - 后端对每个接口二次校验（require_perm），前端只是体验层
 */
export default function AppRoutes() {
  return (
    <Suspense fallback={PageLoading}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />

      <Route
        element={
          <AuthGuard>
            <BasicLayout />
          </AuthGuard>
        }
      >
        {/* 登录后直达智能问数——这是平台的主入口，比概览页更贴近用户的目的 */}
        <Route index element={<Navigate to="/ai-qa" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/profile" element={<ProfilePage />} />

        <Route
          path="/system/users"
          element={
            <PermissionGuard perm="sys:user:view">
              <UserManage />
            </PermissionGuard>
          }
        />
        <Route
          path="/system/roles"
          element={
            <PermissionGuard perm="sys:role:view">
              <RoleManage />
            </PermissionGuard>
          }
        />
        <Route
          path="/system/menus"
          element={
            <PermissionGuard perm="sys:menu:view">
              <MenuManage />
            </PermissionGuard>
          }
        />
        <Route
          path="/system/permissions"
          element={
            <PermissionGuard perm="sys:perm:view">
              <PermissionConfig />
            </PermissionGuard>
          }
        />
        <Route
          path="/system/logs"
          element={
            <PermissionGuard perm="sys:log:view">
              <OperationLog />
            </PermissionGuard>
          }
        />
        <Route
          path="/system/app-config"
          element={
            <PermissionGuard perm="sys:config:view">
              <AppConfig />
            </PermissionGuard>
          }
        />
        <Route
          path="/system/model-config"
          element={
            <PermissionGuard perm="sys:model:view">
              <ModelConfig />
            </PermissionGuard>
          }
        />
        <Route
          path="/feedback/review"
          element={
            <PermissionGuard perm="fb:review:view">
              <FeedbackReview />
            </PermissionGuard>
          }
        />

        <Route
          path="/ai-qa"
          element={
            <PermissionGuard perm="ai:qa">
              <AiQaPage />
            </PermissionGuard>
          }
        />

        {/* 数据台账：路径与 perm 均取自 sys_menu 中已预置的菜单 */}
        <Route
          path="/ledger/commercial"
          element={
            <PermissionGuard perm="lg:commercial:view">
              <CommercialLedger />
            </PermissionGuard>
          }
        />
        <Route
          path="/ledger/ppl"
          element={
            <PermissionGuard perm="lg:ppl:view">
              <PplLedger />
            </PermissionGuard>
          }
        />
        <Route
          path="/ledger/goal"
          element={
            <PermissionGuard perm="lg:goal:view">
              <GoalLedger />
            </PermissionGuard>
          }
        />

        {/* 语义层管理：仅超管可见（语义层直接决定问数准确率） */}
        <Route
          path="/semantic/metrics"
          element={
            <PermissionGuard perm="sem:metric:view">
              <Metrics />
            </PermissionGuard>
          }
        />
        <Route
          path="/semantic/dimensions"
          element={
            <PermissionGuard perm="sem:dimension:view">
              <Dimensions />
            </PermissionGuard>
          }
        />
        <Route
          path="/semantic/rules"
          element={
            <PermissionGuard perm="sem:rule:view">
              <Rules />
            </PermissionGuard>
          }
        />
        <Route
          path="/semantic/fewshots"
          element={
            <PermissionGuard perm="sem:fewshot:view">
              <Fewshots />
            </PermissionGuard>
          }
        />

        <Route path="*" element={<Navigate to="/ai-qa" replace />} />
        </Route>
      </Routes>
    </Suspense>
  );
}
