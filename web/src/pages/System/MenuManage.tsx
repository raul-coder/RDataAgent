import { useCallback, useEffect, useState } from 'react';
import {
  App, Button, Card, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Table, Tag,
} from 'antd';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import * as api from '@/services/api';
import { useAuthStore } from '@/stores/authStore';

interface Row {
  key: number;
  id: number;
  parent_id: number;
  name: string;
  path: string;
  icon: string;
  sort_order: number;
  type: string;
  perm_code: string;
  visible: boolean;
  children?: Row[];
}

function toRows(nodes: api.MenuTreeNode[], parentName = ''): Row[] {
  return nodes.map((n) => ({
    key: n.id,
    id: n.id,
    parent_id: n.parent_id,
    name: n.name,
    path: n.path,
    icon: n.icon,
    sort_order: n.sort_order,
    type: n.type,
    perm_code: n.perm_code,
    visible: n.visible,
    children: n.children?.length ? toRows(n.children) : undefined,
    _parent: parentName,
  })) as Row[];
}

export default function MenuManage() {
  const { message } = App.useApp();
  const hasPerm = useAuthStore((s) => s.hasPerm);
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Row | null>(null);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const tree = await api.fetchMenuTree();
      setRows(toRows(tree));
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { void load(); }, [load]);

  const openCreate = (parentId = 0) => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ parent_id: parentId, type: 'C', sort_order: 0, visible: true });
    setOpen(true);
  };

  const openEdit = (row: Row) => {
    setEditing(row);
    form.setFieldsValue({ ...row });
    setOpen(true);
  };

  const submit = async () => {
    const values = await form.validateFields();
    try {
      if (editing) await api.updateMenu(editing.id, values);
      else await api.createMenu(values);
      message.success('已保存（权限变更将在用户重新登录后生效）');
      setOpen(false);
      void load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const columns: ColumnsType<Row> = [
    { title: '菜单名称', dataIndex: 'name', width: 180 },
    { title: '路由路径', dataIndex: 'path', width: 200 },
    {
      title: '类型',
      dataIndex: 'type',
      width: 80,
      render: (t: string) =>
        t === 'M' ? <Tag>目录</Tag> : t === 'B' ? <Tag color="orange">按钮</Tag> : <Tag color="blue">菜单</Tag>,
    },
    { title: '权限标识', dataIndex: 'perm_code', width: 160 },
    { title: '图标', dataIndex: 'icon', width: 100 },
    { title: '排序', dataIndex: 'sort_order', width: 70 },
    {
      title: '可见',
      dataIndex: 'visible',
      width: 80,
      render: (v: boolean) => (v ? <Tag color="green">是</Tag> : <Tag>否</Tag>),
    },
    {
      title: '操作',
      width: 200,
      render: (_, row) => (
        <Space size={4}>
          <Button type="link" size="small" onClick={() => openCreate(row.id)}>加子项</Button>
          {hasPerm('sys:menu:edit') && (
            <Button type="link" size="small" onClick={() => openEdit(row)}>编辑</Button>
          )}
          {hasPerm('sys:menu:del') && (
            <Popconfirm
              title="确认删除该菜单？"
              onConfirm={async () => {
                try {
                  await api.deleteMenu(row.id);
                  message.success('已删除');
                } catch (e) {
                  message.error((e as Error).message);
                }
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
      title="菜单管理"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>
          {hasPerm('sys:menu:add') && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => openCreate(0)}>新增菜单</Button>
          )}
        </Space>
      }
    >
      <Table<Row>
        size="middle"
        loading={loading}
        columns={columns}
        dataSource={rows}
        pagination={false}
        rowKey="key"
        scroll={{ x: 1080 }}
      />

      <Modal
        open={open}
        title={editing ? '编辑菜单' : '新增菜单'}
        onCancel={() => setOpen(false)}
        onOk={submit}
        destroyOnClose
        okText="保存"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item name="parent_id" label="上级菜单" rules={[{ required: true }]}>
            <InputNumber style={{ width: '100%' }} placeholder="0 表示顶级" />
          </Form.Item>
          <Form.Item name="name" label="菜单名称" rules={[{ required: true, message: '请输入菜单名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="path" label="路由路径">
            <Input placeholder="/system/users" />
          </Form.Item>
          <Form.Item name="type" label="类型" rules={[{ required: true }]}>
            <Select options={[
              { value: 'M', label: '目录' },
              { value: 'C', label: '菜单' },
              { value: 'B', label: '按钮' },
            ]} />
          </Form.Item>
          <Form.Item name="perm_code" label="权限标识" extra="如 sys:user:view；留空表示不鉴权">
            <Input />
          </Form.Item>
          <Form.Item name="icon" label="图标">
            <Input placeholder="robot / setting / user …" />
          </Form.Item>
          <Form.Item name="sort_order" label="排序">
            <InputNumber style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
