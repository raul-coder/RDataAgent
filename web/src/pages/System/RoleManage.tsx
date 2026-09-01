import { useCallback, useEffect, useState } from 'react';
import {
  App, Button, Card, Form, Input, Modal, Popconfirm, Space, Table, Tag,
} from 'antd';
import { PlusOutlined, ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate } from 'react-router-dom';
import * as api from '@/services/api';
import { useAuthStore } from '@/stores/authStore';

export default function RoleManage() {
  const { message } = App.useApp();
  const hasPerm = useAuthStore((s) => s.hasPerm);
  const navigate = useNavigate();

  const [rows, setRows] = useState<api.RoleItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [keyword, setKeyword] = useState('');
  const [loading, setLoading] = useState(false);

  const [editing, setEditing] = useState<api.RoleItem | null>(null);
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.fetchRoles(keyword, page, pageSize);
      setRows(res.items);
      setTotal(res.total);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [keyword, page, pageSize, message]);

  useEffect(() => { void load(); }, [load]);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    setOpen(true);
  };

  const openEdit = (row: api.RoleItem) => {
    setEditing(row);
    form.setFieldsValue({ name: row.name, code: row.code, description: row.description });
    setOpen(true);
  };

  const submit = async () => {
    const values = await form.validateFields();
    try {
      if (editing) {
        await api.updateRole(editing.id, values);
        message.success('已保存');
      } else {
        await api.createRole(values);
        message.success('已创建，请为其配置权限');
      }
      setOpen(false);
      void load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const columns: ColumnsType<api.RoleItem> = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '角色名称', dataIndex: 'name', width: 140 },
    { title: '角色编码', dataIndex: 'code', width: 150 },
    { title: '描述', dataIndex: 'description' },
    { title: '用户数', dataIndex: 'user_count', width: 90 },
    { title: '更新时间', dataIndex: 'updated_at', width: 170 },
    {
      title: '操作',
      width: 240,
      fixed: 'right',
      render: (_, row) => (
        <Space size={4}>
          <Button
            type="link"
            size="small"
            icon={<SafetyCertificateOutlined />}
            onClick={() => navigate(`/system/permissions?roleId=${row.id}`)}
          >
            权限
          </Button>
          {hasPerm('sys:role:edit') && (
            <Button type="link" size="small" onClick={() => openEdit(row)}>编辑</Button>
          )}
          {hasPerm('sys:role:del') && (
            <Popconfirm
              title="确认删除该角色？"
              onConfirm={async () => {
                try {
                  await api.deleteRole(row.id);
                  message.success('已删除');
                } catch (e) {
                  message.error((e as Error).message);
                }
                void load();
              }}
            >
              <Button type="link" size="small" danger disabled={row.is_builtin}>删除</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="角色管理"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>
          {hasPerm('sys:role:add') && (
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增角色</Button>
          )}
        </Space>
      }
    >
      <Space style={{ marginBottom: 16 }}>
        <Input
          allowClear
          placeholder="角色名称"
          style={{ width: 240 }}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onPressEnter={() => { setPage(1); void load(); }}
        />
        <Button type="primary" ghost onClick={() => { setPage(1); void load(); }}>查询</Button>
      </Space>

      <Table<api.RoleItem>
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

      <Modal
        open={open}
        title={editing ? '编辑角色' : '新增角色'}
        onCancel={() => setOpen(false)}
        onOk={submit}
        destroyOnClose
        okText="保存"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item name="name" label="角色名称" rules={[{ required: true, message: '请输入角色名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="code"
            label="角色编码"
            rules={[{ required: true, message: '请输入角色编码' }]}
            extra="英文大写，如 SOUTH_ADMIN；SUPER_ADMIN 拥有全部权限"
          >
            <Input disabled={!!editing} placeholder="SOUTH_ADMIN" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
