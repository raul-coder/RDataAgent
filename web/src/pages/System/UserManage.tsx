import { useCallback, useEffect, useState } from 'react';
import {
  App, Button, Card, DatePicker, Form, Input, Modal, Popconfirm, Select,
  Space, Table, Tag, Typography,
} from 'antd';
import { PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import * as api from '@/services/api';
import { useAuthStore } from '@/stores/authStore';

const { Text } = Typography;

export default function UserManage() {
  const { message } = App.useApp();
  const hasPerm = useAuthStore((s) => s.hasPerm);

  const [rows, setRows] = useState<api.UserItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [keyword, setKeyword] = useState('');
  const [status, setStatus] = useState<number | undefined>();
  const [loading, setLoading] = useState(false);

  const [roles, setRoles] = useState<api.RoleItem[]>([]);
  const [editing, setEditing] = useState<api.UserItem | null>(null);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.fetchUsers({ keyword, status, page, page_size: pageSize });
      setRows(res.items);
      setTotal(res.total);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [keyword, status, page, pageSize, message]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    api.fetchRoles('', 1, 100).then((r) => setRoles(r.items)).catch(() => setRoles([]));
  }, []);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ role_ids: [], password: '123456' });
    setOpen(true);
  };

  const openEdit = (row: api.UserItem) => {
    setEditing(row);
    form.setFieldsValue({
      username: row.username,
      nickname: row.nickname,
      phone: row.phone,
      email: row.email,
      valid_until: row.valid_until ? dayjs(row.valid_until) : null,
      role_ids: row.roles.map((r) => r.id),
    });
    setOpen(true);
  };

  const submit = async () => {
    const values = await form.validateFields();
    const payload: api.UserPayload = {
      username: values.username,
      nickname: values.nickname,
      phone: values.phone,
      email: values.email,
      valid_until: values.valid_until ? values.valid_until.format('YYYY-MM-DD') : '',
      role_ids: values.role_ids ?? [],
    };
    try {
      if (editing) {
        await api.updateUser(editing.id, payload);
        message.success('已保存');
      } else {
        await api.createUser({ ...payload, password: values.password });
        message.success('已创建，初始密码需在首次登录时修改');
      }
      setOpen(false);
      void load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const columns: ColumnsType<api.UserItem> = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '用户名', dataIndex: 'username', width: 120 },
    { title: '昵称', dataIndex: 'nickname', width: 110 },
    { title: '手机号', dataIndex: 'phone', width: 130 },
    {
      title: '角色',
      dataIndex: 'roles',
      width: 130,
      render: (roles: api.RoleBrief[]) =>
        roles.length ? <Tag color="blue">{roles[0].name}</Tag> : <Text type="secondary">-</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (s: number) =>
        s === 1 ? <Tag color="green">启用</Tag> : <Tag color="red">禁用</Tag>,
    },
    { title: '有效期', dataIndex: 'valid_until', width: 120 },
    { title: '最后登录', dataIndex: 'last_login_at', width: 170 },
    {
      title: '操作',
      width: 220,
      fixed: 'right',
      render: (_, row) => (
        <Space size={4}>
          {hasPerm('sys:user:edit') && (
            <Button type="link" size="small" onClick={() => openEdit(row)}>编辑</Button>
          )}
          {hasPerm('sys:user:edit') && (
            <Button
              type="link"
              size="small"
              onClick={async () => {
                await api.toggleUserStatus(row.id);
                message.success('状态已更新');
                void load();
              }}
            >
              {row.status === 1 ? '禁用' : '启用'}
            </Button>
          )}
          {hasPerm('sys:user:del') && (
            <Popconfirm
              title="确认删除该用户？"
              onConfirm={async () => {
                await api.deleteUser(row.id);
                message.success('已删除');
                void load();
              }}
            >
              <Button type="link" size="small" danger>删除</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="用户管理"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>
          {hasPerm('sys:user:add') && (
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增用户</Button>
          )}
        </Space>
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="用户名 / 昵称 / 手机号"
          style={{ width: 240 }}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onPressEnter={() => { setPage(1); void load(); }}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 120 }}
          value={status}
          onChange={(v) => { setStatus(v); setPage(1); }}
          options={[{ value: 1, label: '启用' }, { value: 0, label: '禁用' }]}
        />
        <Button type="primary" ghost onClick={() => { setPage(1); void load(); }}>查询</Button>
        {hasPerm('sys:user:edit') && selected.length > 0 && (
          <Popconfirm
            title={`确认重置所选 ${selected.length} 位用户的密码为 123456？`}
            onConfirm={async () => {
              await api.batchResetPassword(selected);
              message.success('已重置，用户下次登录需修改密码');
              setSelected([]);
              void load();
            }}
          >
            <Button danger>批量重置密码</Button>
          </Popconfirm>
        )}
      </Space>

      <Table<api.UserItem>
        rowKey="id"
        size="middle"
        loading={loading}
        columns={columns}
        dataSource={rows}
        scroll={{ x: 1180 }}
        rowSelection={{
          selectedRowKeys: selected,
          onChange: (keys) => setSelected(keys as number[]),
        }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => { setPage(p); setPageSize(ps); },
        }}
      />

      <Modal
        open={open}
        title={editing ? '编辑用户' : '新增用户'}
        onCancel={() => setOpen(false)}
        onOk={submit}
        destroyOnClose
        okText="保存"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input disabled={!!editing} placeholder="登录账号" />
          </Form.Item>
          {!editing && (
            <Form.Item name="password" label="初始密码" rules={[{ required: true, message: '请输入初始密码' }]}>
              <Input placeholder="默认 123456" />
            </Form.Item>
          )}
          <Form.Item name="nickname" label="昵称">
            <Input />
          </Form.Item>
          <Form.Item name="phone" label="手机号">
            <Input />
          </Form.Item>
          <Form.Item name="email" label="邮箱">
            <Input />
          </Form.Item>
          <Form.Item name="valid_until" label="账号有效期">
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="role_ids" label="角色" rules={[{ required: true, message: '请选择角色' }]}>
            <Select
              mode="multiple"
              placeholder="可多选"
              options={roles.map((r) => ({ value: r.id, label: r.name }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
