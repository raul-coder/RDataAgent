import { Button, Dropdown, Space, Tooltip } from 'antd';
import {
  ArrowDownOutlined, ArrowUpOutlined, DownloadOutlined,
  BarChartOutlined, PieChartOutlined, LineChartOutlined,
} from '@ant-design/icons';
import styles from './ResultToolbar.module.css';

interface Props {
  onSort: (dir: 'asc' | 'desc') => void;
  onChart: (type: 'pie' | 'bar' | 'line') => void;
  onExport: () => void;
  disabled?: boolean;
}

/**
 * 结果操作条：排序 / 换图 / 导出。
 * 这些走「结果二次加工」意图 —— 后端直接变换缓存结果集，不重跑 SQL（毫秒级）。
 */
export default function ResultToolbar({ onSort, onChart, onExport, disabled }: Props) {
  return (
    <div className={styles.bar}>
      <Space size={4}>
        <Tooltip title="降序">
          <Button size="small" type="text" icon={<ArrowDownOutlined />}
                  disabled={disabled} onClick={() => onSort('desc')}>
            降序
          </Button>
        </Tooltip>
        <Tooltip title="升序">
          <Button size="small" type="text" icon={<ArrowUpOutlined />}
                  disabled={disabled} onClick={() => onSort('asc')}>
            升序
          </Button>
        </Tooltip>

        <Dropdown
          trigger={['click']}
          menu={{
            items: [
              { key: 'pie', icon: <PieChartOutlined />, label: '饼图' },
              { key: 'bar', icon: <BarChartOutlined />, label: '柱状图' },
              { key: 'line', icon: <LineChartOutlined />, label: '折线图' },
            ],
            onClick: ({ key }) => onChart(key as 'pie' | 'bar' | 'line'),
          }}
        >
          <Button size="small" type="text" icon={<BarChartOutlined />} disabled={disabled}>
            换图
          </Button>
        </Dropdown>

        <Tooltip title="导出 CSV">
          <Button size="small" type="text" icon={<DownloadOutlined />}
                  disabled={disabled} onClick={onExport}>
            导出
          </Button>
        </Tooltip>
      </Space>
    </div>
  );
}
