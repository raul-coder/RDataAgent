import { useCallback, useEffect, useState } from 'react';
import {
  App, Badge, Button, Card, Descriptions, Drawer, Form, Input, Modal, Select,
  Space, Table, Tag, Typography,
} from 'antd';
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import type { ColumnsType } from 'antd/es/table';
import * as api from '@/services/api';

const { Text, Paragraph } = Typography;

const STATUS_OPTIONS = [
  { value: '待处理', label: '待处理' },
  { value: '处理中', label: '处理中' },
  { value: '已处理', label: '已处理' },
  { value: '已忽略', label: '已忽略' },
];

function statusColor(status: string) {
  if (status === '已处理') return 'green';
  if (status === '处理中') return 'blue';
  if (status === '已忽略') return 'default';
  return 'orange';
}

/** 回复校对：UC-4 管理员侧闭环（查看 → 处理 → 状态实时变更） */
export default function FeedbackReview() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [rows, setRows] = useState<api.FeedbackItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [status, setStatus] = useState<string | undefined>();
  const [keyword, setKeyword] = useState('');
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<api.FeedbackStats | null>(null);
  const [users, setUsers] = useState<string[]>([]);

  const [detail, setDetail] = useState<api.FeedbackItem | null>(null);
  const [handling, setHandling] = useState<api.FeedbackItem | null>(null);
  const [nextStatus, setNextStatus] = useState('已处理');
  const [remark, setRemark] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.fetchFeedbacks({ status, keyword, page, page_size: pageSize });
      setRows(res.items);
      setTotal(res.total);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [status, keyword, page, pageSize, message]);

  // 统计与用户下拉变化频率低，只在挂载时拉一次
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    api.fetchFeedbackStats().then(setStats).catch(() => undefined);
    api.fetchFeedbackUsers().then(setUsers).catch(() => undefined);
  }, []);

  const submitHandle = async () => {
    if (!handling) return;
    setSubmitting(true);
    try {
      await api.handleFeedback(handling.id, nextStatus, remark);
      message.success('已处理');
      setHandling(null);
      setRemark('');
      void load();
      api.fetchFeedbackStats().then(setStats).catch(() => undefined);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const columns: ColumnsType<api.FeedbackItem> = [
    {
      title: '序号', width: 70,
      render: (_, __, i) => (page - 1) * pageSize + i + 1,
    },
    { title: '用户', dataIndex: 'username', width: 110 },
    {
      title: '问题', dataIndex: 'question', ellipsis: true,
      render: (q: string) => <Text>{q}</Text>,
    },
    {
      title: '状态', dataIndex: 'status', width: 100,
      render: (s: string) => <Tag color={statusColor(s)}>{s}</Tag>,
    },
    { title: '提交时间', dataIndex: 'created_at', width: 175 },
    {
      title: '操作', width: 180, fixed: 'right',
      render: (_, r) => (
        <Space size={4}>
          <Button type="link" size="small" onClick={() => setDetail(r)}>查看</Button>
          <Button
            type="link"
            size="small"
            disabled={r.status === '已处理'}
            onClick={() => {
              setHandling(r);
              setNextStatus('已处理');
              setRemark(r.remark || '');
            }}
          >
            处理
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title={(
        <Space>
          回复校对
          {stats && (
            <Badge
              count={stats.todo}
              showZero
              color="orange"
              title={`${stats.todo} 条待处理`}
            />
          )}
        </Space>
      )}
      extra={<Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>}
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="问题或用户名"
          style={{ width: 220 }}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onPressEnter={() => { setPage(1); void load(); }}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 130 }}
          value={status}
          onChange={(v) => { setStatus(v); setPage(1); }}
          options={STATUS_OPTIONS}
        />
        <Select
          allowClear
          showSearch
          placeholder="按用户筛选"
          style={{ width: 160 }}
          value={keyword && users.includes(keyword) ? keyword : undefined}
          onChange={(v) => { setKeyword(v ?? ''); setPage(1); }}
          options={users.map((u) => ({ value: u, label: u }))}
        />
        <Button type="primary" ghost onClick={() => { setPage(1); void load(); }}>查询</Button>
        {stats && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            共 {stats.total} 条 · 待处理 {stats.todo} · 已处理 {stats.done}
          </Text>
        )}
      </Space>

      <Table<api.FeedbackItem>
        rowKey="id"
        size="middle"
        loading={loading}
        columns={columns}
        dataSource={rows}
        scroll={{ x: 1000 }}
        pagination={{
          current: page, pageSize, total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => { setPage(p); setPageSize(ps); },
        }}
      />

      {/* 详情抽屉：完整 AI 回答快照 + 跳转原会话回放 */}
      <Drawer
        title="反馈单详情"
        width={640}
        open={!!detail}
        onClose={() => setDetail(null)}
        extra={detail?.session_id ? (
          <Button
            type="primary"
            onClick={() => navigate(`/ai-qa?session=${detail.session_id}`)}
          >
            跳转到会话回放
          </Button>
        ) : null}
      >
        {detail && (
          <>
            <Descriptions column={1} size="small" bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="反馈单号">{detail.id}</Descriptions.Item>
              <Descriptions.Item label="提交用户">{detail.username}</Descriptions.Item>
              <Descriptions.Item label="提交时间">{detail.created_at}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={statusColor(detail.status)}>{detail.status}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="会话 ID">
                {detail.session_id ?? '—'}
              </Descriptions.Item>
              <Descriptions.Item label="处理备注">{detail.remark || '—'}</Descriptions.Item>
            </Descriptions>

            <Paragraph strong style={{ marginBottom: 4 }}>用户问题</Paragraph>
            <Paragraph style={{ background: '#fafafa', padding: 12, borderRadius: 6 }}>
              {detail.question}
            </Paragraph>

            <Paragraph strong style={{ marginBottom: 4 }}>AI 回答快照</Paragraph>
            <Paragraph style={{ background: '#fafafa', padding: 12, borderRadius: 6, whiteSpace: 'pre-wrap' }}>
              {detail.ai_reply || '（无快照）'}
            </Paragraph>
          </>
        )}
      </Drawer>

      {/* 处理弹窗 */}
      <Modal
        title={`处理反馈单 #${handling?.id ?? ''}`}
        open={!!handling}
        onCancel={() => setHandling(null)}
        onOk={() => void submitHandle()}
        confirmLoading={submitting}
        okText="提交"
        cancelText="取消"
        width={560}
        destroyOnClose
      >
        {handling && (
          <Form layout="vertical" style={{ marginTop: 16 }}>
            <Form.Item label="用户问题">
              <Paragraph style={{ margin: 0 }}>{handling.question}</Paragraph>
            </Form.Item>
            <Form.Item label="处理状态">
              <Select
                value={nextStatus}
                onChange={setNextStatus}
                options={STATUS_OPTIONS}
              />
            </Form.Item>
            <Form.Item label="处理备注">
              <Input.TextArea
                rows={4}
                maxLength={500}
                showCount
                value={remark}
                placeholder="例如：已核对，指标口径应为「商业收入」而非「合同额」，已修正语义层"
                onChange={(e) => setRemark(e.target.value)}
              />
            </Form.Item>
          </Form>
        )}
      </Modal>
    </Card>
  );
}
