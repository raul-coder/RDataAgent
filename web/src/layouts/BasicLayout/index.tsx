import { useMemo, useState } from 'react';
import { Avatar, Dropdown, Layout, Menu, Typography, theme } from 'antd';
import type { MenuProps } from 'antd';
import {
  BellOutlined, DashboardOutlined, LogoutOutlined, UserOutlined,
} from '@ant-design/icons';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/stores/authStore';
import type { MenuNode } from '@/services/api';
import { ICON_MAP } from '@/utils/icons';
import styles from './index.module.css';

const { Header, Sider, Content } = Layout;
const { Text } = Typography;

/** 后端返回的是扁平菜单列表，这里还原成 antd Menu 需要的树形结构 */
function buildTree(menus: MenuNode[]): Required<MenuProps>['items'] {
  const visible = menus.filter((m) => m.visible !== false);
  const convert = (parentId: number): Required<MenuProps>['items'] =>
    visible
      .filter((m) => (m.parent_id || 0) === parentId)
      .sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name))
      .map((m) => {
        const children = convert(m.id);
        const base = {
          key: m.path || String(m.id),
          icon: ICON_MAP[m.icon] ?? <DashboardOutlined />,
          label: m.name,
        };
        // 注意：children 必须整体省略，不能传 undefined，否则 antd 类型报错
        return children.length ? { ...base, children } : base;
      });
  return convert(0);
}

export default function BasicLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuthStore();
  const { token } = theme.useToken();

  const items = useMemo(() => buildTree(user?.menus ?? []), [user?.menus]);

  // 当前选中：优先精确匹配，其次匹配前缀（父级高亮）
  const selectedKeys = useMemo(() => {
    const paths = (user?.menus ?? []).map((m) => m.path).filter(Boolean);
    const exact = paths.find((p) => p === location.pathname);
    if (exact) return [exact];
    const prefix = paths
      .filter((p) => p !== '/' && location.pathname.startsWith(p))
      .sort((a, b) => b.length - a.length);
    return prefix.length ? [prefix[0]] : [];
  }, [location.pathname, user?.menus]);

  return (
    <Layout className={styles.layout}>
      <Header className={styles.header}>
        <div className={styles.brand}>
          <DashboardOutlined /> 经管之星
        </div>
        <div className={styles.right}>
          <span className={styles.iconBtn}>
            <BellOutlined />
            <i className={styles.badgeDot} />
          </span>
          <Dropdown
            menu={{
              items: [
                { key: 'profile', icon: <UserOutlined />, label: '个人信息' },
                { key: 'version', label: `v1.0.0 · ${user?.role_codes?.[0] ?? ''}`, disabled: true },
                { type: 'divider' },
                { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true },
              ],
              onClick: async ({ key }) => {
                if (key === 'logout') {
                  await logout();
                  navigate('/login', { replace: true });
                } else if (key === 'profile') {
                  navigate('/profile');
                }
              },
            }}
            placement="bottomRight"
          >
            <span className={styles.user}>
              <Avatar size={30} icon={<UserOutlined />} style={{ background: token.colorPrimary }} />
              <Text className={styles.userName}>{user?.nickname || user?.username}</Text>
            </span>
          </Dropdown>
        </div>
      </Header>

      <Layout>
        <Sider
          width={200}
          collapsible
          collapsed={collapsed}
          onCollapse={setCollapsed}
          className={styles.sider}
          trigger={null}
        >
          <Menu
            mode="inline"
            selectedKeys={selectedKeys}
            items={items}
            className={styles.menu}
            onClick={({ key }) => key.startsWith('/') && navigate(key)}
          />
          <div className={styles.siderFooter}>
            <button className={styles.toggleBtn} onClick={() => setCollapsed(!collapsed)}>
              {collapsed ? '»' : '« 收起'}
            </button>
          </div>
        </Sider>

        <Content className={styles.content}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
