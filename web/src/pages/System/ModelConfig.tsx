import { useCallback, useEffect, useState } from 'react';
import {
  App, Alert, AutoComplete, Button, Card, Form, Input, Modal, Select, Space, Switch, Table,
  Tag, Typography,
} from 'antd';
import { PlusOutlined, ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import * as api from '@/services/api';

const { Text } = Typography;

const PROVIDERS = [
  { value: 'openai', label: 'OpenAI 兼容端点' },
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'qwen', label: '通义千问（百炼）' },
  { value: 'glm', label: '智谱 GLM' },
  { value: 'moonshot', label: 'Moonshot' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'ollama', label: 'Ollama（本地）' },
];

/** 常见端点，手填 Base URL 容易写错，给个快捷选择 */
const BASE_URL_PRESETS = [
  { value: 'https://dashscope.aliyuncs.com/compatible-mode/v1', label: '阿里云百炼（兼容模式）' },
  { value: 'https://api.deepseek.com/v1', label: 'DeepSeek 官方' },
  { value: 'https://api.openai.com/v1', label: 'OpenAI 官方' },
  { value: 'https://open.bigmodel.cn/api/paas/v4', label: '智谱 GLM' },
  { value: 'http://localhost:11434/v1', label: 'Ollama 本地' },
];

const emptyForm: api.ModelPayload = {
  name: '', provider: 'openai', base_url: '', model_name: '',
  api_key: '', scene: 'chat_qa', is_default: false, enabled: true,
};

export default function ModelConfig() {
  const { message, modal } = App.useApp();
  const [rows, setRows] = useState<api.ModelItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<api.ModelItem | null>(null);
  const [form, setForm] = useState<api.ModelPayload>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<api.TestConnResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await api.fetchModels());
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { void load(); }, [load]);

  const openCreate = () => {
    setEditing(null);
    setForm(emptyForm);
    setTestResult(null);
    setOpen(true);
  };

  const openEdit = (row: api.ModelItem) => {
    setEditing(row);
    setForm({
      name: row.name, provider: row.provider, base_url: row.base_url,
      model_name: row.model_name, api_key: '', scene: row.scene,
      is_default: row.is_default, enabled: row.enabled,
    });
    setTestResult(null);
    setOpen(true);
  };

  const submit = async () => {
    if (!form.name || !form.model_name || !form.base_url) {
      message.warning('名称、Base URL、模型名称不能为空');
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await api.updateModel(editing.id, form);
        message.success('已保存');
      } else {
        await api.createModel(form);
        message.success('已新增');
      }
      setOpen(false);
      void load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const testConn = async () => {
    if (!form.model_name || !form.base_url) {
      message.warning('请先填写 Base URL 与模型名称');
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api.testModelConnection({
        base_url: form.base_url,
        model_name: form.model_name,
        api_key: form.api_key || undefined,
        provider: form.provider,
      });
      setTestResult(res);
    } catch (e) {
      setTestResult({ ok: false, cost_ms: 0, message: (e as Error).message });
    } finally {
      setTesting(false);
    }
  };

  const testSaved = async (row: api.ModelItem) => {
    try {
      const res = await api.testSavedModel(row.id);
      if (res.ok) {
        message.success(`${row.name} 连接成功（${res.cost_ms}ms）`);
      } else {
        modal.error({ title: `${row.name} 连接失败`, content: res.message });
      }
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const setDefault = async (row: api.ModelItem) => {
    try {
      await api.setDefaultModel(row.id);
      message.success(`已将「${row.name}」设为智能问数默认模型`);
      void load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const remove = (row: api.ModelItem) => {
    modal.confirm({
      title: `删除模型「${row.name}」？`,
      content: '删除后使用该模型的问数请求将回退到降级链中的下一个模型。',
      okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
      onOk: async () => {
        await api.deleteModel(row.id);
        message.success('已删除');
        void load();
      },
    });
  };

  const columns: ColumnsType<api.ModelItem> = [
    {
      title: '名称', dataIndex: 'name', width: 180,
      render: (name: string, r) => (
        <Space>
          {name}
          {r.is_default && <Tag color="gold">默认</Tag>}
        </Space>
      ),
    },
    { title: '模型', dataIndex: 'model_name', width: 200, ellipsis: true },
    { title: 'Base URL', dataIndex: 'base_url', ellipsis: true },
    {
      title: '密钥', dataIndex: 'has_key', width: 100,
      render: (has: boolean, r) => (has
        ? <Text type="secondary" style={{ fontSize: 12 }}>{r.api_key_masked}</Text>
        : <Tag color="red">未配置</Tag>),
    },
    {
      title: '启用', dataIndex: 'enabled', width: 80,
      render: (v: boolean) => (v ? <Tag color="green">是</Tag> : <Tag>否</Tag>),
    },
    {
      title: '操作', width: 260, fixed: 'right',
      render: (_, r) => (
        <Space size={4}>
          <Button type="link" size="small" onClick={() => void testSaved(r)}>测试</Button>
          <Button type="link" size="small" disabled={r.is_default} onClick={() => void setDefault(r)}>
            设默认
          </Button>
          <Button type="link" size="small" onClick={() => openEdit(r)}>编辑</Button>
          <Button type="link" size="small" danger onClick={() => remove(r)}>删除</Button>
        </Space>
      ),
    },
  ];

  const defaultModel = rows.find((r) => r.is_default);

  return (
    <Card
      title="模型配置"
      extra={(
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增模型</Button>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>
        </Space>
      )}
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message={(
          <Space wrap>
            <span>智能问数当前使用：</span>
            <Select
              size="small"
              style={{ minWidth: 260 }}
              placeholder="未设置默认模型"
              value={defaultModel?.id}
              onChange={(id) => {
                const row = rows.find((r) => r.id === id);
                if (row) void setDefault(row);
              }}
              options={rows.map((r) => ({
                value: r.id,
                label: `${r.name}（${r.model_name}）${r.enabled ? '' : ' · 已禁用'}`,
              }))}
            />
          </Space>
        )}
        description="切换默认模型立即生效，无需重启。未配置 Key 的模型不会进入问数降级链。"
      />

      <Table<api.ModelItem>
        rowKey="id"
        size="middle"
        loading={loading}
        columns={columns}
        dataSource={rows}
        scroll={{ x: 1100 }}
        pagination={false}
      />

      <Modal
        title={editing ? `编辑模型 · ${editing.name}` : '新增模型'}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => void submit()}
        confirmLoading={saving}
        okText="保存"
        cancelText="取消"
        width={620}
        destroyOnClose
      >
        <Form layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item label="显示名称" required>
            <Input
              value={form.name}
              placeholder="例如：DeepSeek-V4-Flash"
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </Form.Item>

          <Form.Item label="服务商">
            <Select
              value={form.provider}
              options={PROVIDERS}
              onChange={(v) => setForm({ ...form, provider: v })}
            />
          </Form.Item>

          <Form.Item
            label="Base URL"
            required
            extra="各家基本都提供 OpenAI 兼容端点，填错会导致调用失败。支持选择预设或手填任意 URL"
          >
            <AutoComplete
              value={form.base_url}
              placeholder="选择预设或手动输入"
              options={BASE_URL_PRESETS}
              allowClear
              filterOption={(inputValue, option) =>
                (option?.value?.toLowerCase().includes(inputValue.toLowerCase()) ?? false)
                || (option?.label?.toLowerCase().includes(inputValue.toLowerCase()) ?? false)}
              onChange={(v) => setForm({ ...form, base_url: v ?? '' })}
              onSelect={(v) => setForm({ ...form, base_url: v })}
              // AutoComplete 输入后不一定会触发 onChange；失焦时兜底同步一次，
              // 否则用户手填 URL 后直接点保存，form.base_url 仍是旧的。
              onBlur={(e) => setForm({ ...form, base_url: (e.target as HTMLInputElement).value })}
            />
          </Form.Item>

          <Form.Item label="模型名称" required>
            <Input
              value={form.model_name}
              placeholder="例如：deepseek-v4-flash"
              onChange={(e) => setForm({ ...form, model_name: e.target.value })}
            />
          </Form.Item>

          <Form.Item
            label="API Key"
            extra={editing
              ? `已保存：${editing.api_key_masked}（留空表示不修改）`
              : '将以加密形式存储，列表页只展示脱敏串'}
          >
            <Input.Password
              value={form.api_key}
              placeholder={editing ? '留空则不修改' : 'sk-...'}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
            />
          </Form.Item>

          <Space>
            <Button
              icon={<ThunderboltOutlined />}
              loading={testing}
              onClick={() => void testConn()}
            >
              测试连接
            </Button>
            {testResult && (
              <Text type={testResult.ok ? 'success' : 'danger'} style={{ fontSize: 12 }}>
                {testResult.message}
              </Text>
            )}
          </Space>

          {testResult && !testResult.ok && (
            <Alert
              type="error"
              showIcon
              style={{ marginTop: 12 }}
              message="连接失败"
              description={testResult.message}
            />
          )}

          <Space size={24} style={{ marginTop: 16 }}>
            <Space>
              <Switch
                checked={form.enabled}
                onChange={(v) => setForm({ ...form, enabled: v })}
              />
              <Text>启用</Text>
            </Space>
            <Space>
              <Switch
                checked={form.is_default}
                onChange={(v) => setForm({ ...form, is_default: v })}
              />
              <Text>设为默认</Text>
            </Space>
          </Space>
        </Form>
      </Modal>
    </Card>
  );
}
