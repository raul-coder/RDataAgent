import {
  AppstoreOutlined, BarChartOutlined, BlockOutlined, BookOutlined, ControlOutlined,
  DashboardOutlined, DatabaseOutlined, ExperimentOutlined, ExclamationCircleOutlined,
  FileTextOutlined, FlagOutlined, MenuOutlined, ProfileOutlined, RobotOutlined,
  SafetyCertificateOutlined, SettingOutlined, TableOutlined, TeamOutlined, UserOutlined,
} from '@ant-design/icons';

/** 后端菜单 icon 字段（字符串）→ antd 图标组件 */
export const ICON_MAP: Record<string, React.ReactNode> = {
  robot: <RobotOutlined />,
  setting: <SettingOutlined />,
  control: <ControlOutlined />,
  cube: <BlockOutlined />,
  user: <UserOutlined />,
  team: <TeamOutlined />,
  menu: <MenuOutlined />,
  'safety-certificate': <SafetyCertificateOutlined />,
  'file-text': <FileTextOutlined />,
  flag: <FlagOutlined />,
  'exclamation-circle': <ExclamationCircleOutlined />,
  database: <DatabaseOutlined />,
  table: <TableOutlined />,
  book: <BookOutlined />,
  'bar-chart': <BarChartOutlined />,
  appstore: <AppstoreOutlined />,
  profile: <ProfileOutlined />,
  experiment: <ExperimentOutlined />,
};

export const FALLBACK_ICON = <DashboardOutlined />;
