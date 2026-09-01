import { useCallback, useEffect, useState } from 'react';
import { App, Card, DatePicker, Input, Select, Space, Table, Tag, Button } from 'antd';
import { DownloadOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';
import type { ColumnsType } from 'antd/es/table';
import * as api from '@/services/api';

const LOG_TYPES = [
  { value: 'login', label: '登录日志' },
  { value: 'oper', label: '操作日志' },
];

function statusColor(status: string) {
  if (status === '成功') return 'green';
  if (status.startsWith('失败')) return 'red';
  return 'orange';
}

export default function OperationLog() {
  const { message } = App.useApp();
  const [rows, setRows] = useState<api.OperLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [keyword, setKeyword] = useState('');
  const [username, setUsername] = useState('');
  const [logType, setLogType] = useState<string | undefined>();
  const [status, setStatus] = useState<string | undefined>();
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);

  const filters = {
    keyword,
    username,
    log_type: logType,
    status,
    start_time: range?.[0] ? range[0].format('YYYY-MM-DD') : undefined,
    end_time: range?.[1] ? range[1].format('YYYY-MM-DD') : undefined,
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.fetchOperLogs({ ...filters, page, page_size: pageSize });
      setRows(res.items);
      setTotal(res.total);
    } finally {
      setLoading(false);
    }
    // filters 是每次渲染新建的对象，故按字段显式声明依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keyword, username, logType, status, range, page, pageSize]);

  useEffect(() => { void load(); }, [load]);

  const exportCsv = async () => {
    setExporting(true);
    try {
      await api.downloadOperLogs(filters);
      message.success('导出已开始，请查看浏览器下载');
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setExporting(false);
    }
  };

  const columns: ColumnsType<api.OperLogItem> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    {
      title: '类型',
      dataIndex: 'log_type',
      width: 90,
      render: (t: string) => (t === 'login' ? <Tag color="blue">登录</Tag> : <Tag>操作</Tag>),
    },
    { title: '用户', dataIndex: 'username', width: 110 },
    { title: '动作', dataIndex: 'action', width: 240 },
    { title: 'IP', dataIndex: 'ip', width: 140 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 140,
      render: (s: string) => <Tag color={statusColor(s)}>{s}</Tag>,
    },
    { title: '耗时(ms)', dataIndex: 'cost_ms', width: 100 },
    { title: '时间', dataIndex: 'created_at', width: 175 },
  ];

  return (
    <Card
      title="操作日志"
      extra={<Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>}
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="动作关键词"
          style={{ width: 200 }}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onPressEnter={() => { setPage(1); void load(); }}
        />
        <Input
          allowClear
          placeholder="用户名"
          style={{ width: 140 }}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          onPressEnter={() => { setPage(1); void load(); }}
        />
        <Select
          allowClear
          placeholder="日志类型"
          style={{ width: 130 }}
          value={logType}
          onChange={(v) => { setLogType(v); setPage(1); }}
          options={LOG_TYPES}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 130 }}
          value={status}
          onChange={(v) => { setStatus(v); setPage(1); }}
          options={[
            { value: '成功', label: '成功' },
            { value: '部分成功', label: '部分成功' },
            { value: '失败-密码错误', label: '失败-密码错误' },
            { value: '失败-账号锁定', label: '失败-账号锁定' },
          ]}
        />
        <DatePicker.RangePicker
          value={range}
          onChange={(v) => { setRange(v); setPage(1); }}
          placeholder={['开始日期', '结束日期']}
          allowClear
        />
        <Button type="primary" ghost onClick={() => { setPage(1); void load(); }}>查询</Button>
        <Button
          icon={<DownloadOutlined />}
          loading={exporting}
          onClick={() => void exportCsv()}
        >
          导出 CSV
        </Button>
      </Space>

      <Table<api.OperLogItem>
        rowKey="id"
        size="middle"
        loading={loading}
        columns={columns}
        dataSource={rows}
        scroll={{ x: 1080 }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => { setPage(p); setPageSize(ps); },
        }}
      />
    </Card>
  );
}
