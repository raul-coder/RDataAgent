import { useCallback, useEffect, useState } from 'react';
import {
  App, Button, Drawer, Empty, Input, List, Select, Space, Spin, Tag, Typography,
} from 'antd';
import { CopyOutlined, SearchOutlined } from '@ant-design/icons';
import * as api from '@/services/api';

const { Text, Paragraph } = Typography;

const DAY_OPTIONS = [
  { value: 7, label: '最近 7 天' },
  { value: 30, label: '最近 30 天' },
  { value: 90, label: '最近 90 天' },
  { value: 0, label: '全部' },
];

/**
 * 问数日志（FR-Q27）：会话维度的日志列表 + 详情回放。
 * 侧边栏宽度有限，因此列表只放关键列，完整回放放进抽屉。
 */
export default function LogList() {
  const { message } = App.useApp();
  const [rows, setRows] = useState<api.ChatSessionItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [days, setDays] = useState(30);
  const [username, setUsername] = useState('');
  const [keyword, setKeyword] = useState('');
  const [loading, setLoading] = useState(false);

  const [detail, setDetail] = useState<api.SessionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.fetchChatLogs({
        days, username, keyword, page, page_size: 20,
      });
      setRows(res.items);
      setTotal(res.total);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [days, username, keyword, page, message]);

  useEffect(() => { void load(); }, [load]);

  const openDetail = async (id: number) => {
    setDetail(null);
    setDetailLoading(true);
    try {
      setDetail(await api.fetchSessionDetail(id));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setDetailLoading(false);
    }
  };

  const copyId = async (id: number) => {
    try {
      await navigator.clipboard.writeText(String(id));
      message.success(`会话 ID ${id} 已复制`);
    } catch {
      message.warning('浏览器拒绝了剪贴板访问，请手动复制');
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <Space direction="vertical" size={8} style={{ padding: 12 }}>
        <Select
          size="small"
          style={{ width: '100%' }}
          value={days}
          options={DAY_OPTIONS}
          onChange={(v) => { setDays(v); setPage(1); }}
        />
        <Input
          size="small"
          allowClear
          prefix={<SearchOutlined />}
          placeholder="标题关键词"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onPressEnter={() => { setPage(1); void load(); }}
        />
        <Input
          size="small"
          allowClear
          placeholder="用户名"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          onPressEnter={() => { setPage(1); void load(); }}
        />
        <Text type="secondary" style={{ fontSize: 12 }}>共 {total} 条会话</Text>
      </Space>

      <div style={{ flex: 1, overflowY: 'auto', padding: '0 8px 12px' }}>
        {loading ? (
          <div style={{ textAlign: 'center', padding: 24 }}><Spin /></div>
        ) : rows.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无问数记录" />
        ) : (
          <List
            size="small"
            dataSource={rows}
            renderItem={(r) => (
              <List.Item
                style={{ cursor: 'pointer', padding: '8px 6px', display: 'block' }}
                onClick={() => void openDetail(r.id)}
              >
                <div style={{ fontWeight: 500, marginBottom: 4 }} title={r.title}>
                  {r.title}
                </div>
                <Space size={4} wrap>
                  <Text type="secondary" style={{ fontSize: 11 }}>{r.username}</Text>
                  <Text type="secondary" style={{ fontSize: 11 }}>·</Text>
                  <Text type="secondary" style={{ fontSize: 11 }}>{r.msg_count} 条</Text>
                  {r.user_feedback && <Tag color="blue" style={{ marginInlineEnd: 0 }}>{r.user_feedback}</Tag>}
                  {r.admin_feedback && <Tag color="green" style={{ marginInlineEnd: 0 }}>{r.admin_feedback}</Tag>}
                </Space>
                <div>
                  <Text type="secondary" style={{ fontSize: 11 }}>{r.last_msg_at}</Text>
                </div>
              </List.Item>
            )}
          />
        )}
        {rows.length > 0 && (
          <Space style={{ width: '100%', justifyContent: 'center', marginTop: 8 }}>
            <Button size="small" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</Button>
            <Text type="secondary" style={{ fontSize: 12 }}>{page}/{Math.ceil(total / 20) || 1}</Text>
            <Button
              size="small"
              disabled={page * 20 >= total}
              onClick={() => setPage(page + 1)}
            >
              下一页
            </Button>
          </Space>
        )}
      </div>

      <Drawer
        title="会话回放"
        width={720}
        open={!!detail || detailLoading}
        onClose={() => setDetail(null)}
        extra={detail && (
          <Button icon={<CopyOutlined />} onClick={() => void copyId(detail.id)}>
            复制会话 ID
          </Button>
        )}
      >
        {detailLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
        ) : detail ? (
          <>
            <Paragraph type="secondary">
              会话 #{detail.id} · {detail.username} · {detail.msg_count} 条消息 · {detail.last_msg_at}
            </Paragraph>
            {detail.messages.map((m) => (
              <div
                key={m.id}
                style={{
                  marginBottom: 12,
                  padding: 12,
                  borderRadius: 8,
                  background: m.role === 'user' ? '#e6f4ff' : '#fafafa',
                }}
              >
                <div style={{ marginBottom: 4 }}>
                  <Text strong>{m.role === 'user' ? '用户' : 'AI'}</Text>
                  <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>{m.created_at}</Text>
                </div>
                <div style={{ whiteSpace: 'pre-wrap' }}>{m.content}</div>
                {m.payload?.sql && (
                  <details style={{ marginTop: 8 }}>
                    <summary style={{ cursor: 'pointer', fontSize: 12 }}>查看生成 SQL</summary>
                    <pre style={{ fontSize: 11, overflowX: 'auto', marginTop: 6 }}>
                      {m.payload.sql}
                    </pre>
                  </details>
                )}
              </div>
            ))}
          </>
        ) : null}
      </Drawer>
    </div>
  );
}
