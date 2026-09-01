import { useCallback, useEffect, useState } from 'react';
import {
  App, Button, Form, Input, InputNumber, Modal, Popconfirm, Select,
  Space, Switch, Table, Tag, Tooltip, Typography,
} from 'antd';
import {
  DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import styles from './CrudTable.module.css';

const { Text } = Typography;

/* eslint-disable @typescript-eslint/no-explicit-any */

export type FieldType = 'text' | 'textarea' | 'number' | 'switch' | 'tags';

/**
 * 行数据的字段由 fields 配置决定，不同资源的列完全不同，
 * 无法用静态类型描述，因此这里用宽松类型（配置驱动组件的固有取舍）。
 */
type Row = Record<string, any>;

export interface Field {
  name: string;
  label: string;
  type: FieldType;
  required?: boolean;
  ellipsis?: boolean;
  width?: number;
  /** 表单里的补充说明 */
  hint?: string;
}

interface Props {
  title: string;
  fields: Field[];
  /** 无编辑权限时只展示，不出现增删改按钮 */
  canEdit: boolean;
  deleteText?: string;
  fetchAll: () => Promise<Row[]>;
  onCreate: (values: Row) => Promise<unknown>;
  onUpdate: (id: number, values: Row) => Promise<unknown>;
  onDelete: (id: number) => Promise<unknown>;
  /** 额外的行操作（如 Few-shot 的「验证 SQL」） */
  extraActions?: (row: Row, reload: () => void) => React.ReactNode;
}

/**
 * 配置驱动的增删改查表格。
 *
 * 语义层四类资源（指标 / 维度 / 口径规则 / Few-shot）结构各异，
 * 但操作模式完全一致，因此把列定义与请求函数抽成配置，
 * 避免为四类各写一遍几乎相同的表格与弹窗。
 */
export default function CrudTable({
  title, fields, canEdit, deleteText = '停用',
  fetchAll, onCreate, onUpdate, onDelete, extraActions,
}: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<Row | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await fetchAll());
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [fetchAll, message]);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    // 开关类字段默认启用，否则新增后立刻是停用状态，容易被误以为没保存上
    const defaults: Row = {};
    fields.forEach((f) => {
      if (f.type === 'switch') defaults[f.name] = true;
      if (f.type === 'tags') defaults[f.name] = [];
    });
    form.setFieldsValue(defaults);
    setOpen(true);
  };

  const openEdit = (row: Row) => {
    setEditing(row);
    form.resetFields();
    form.setFieldsValue(row);
    setOpen(true);
  };

  const submit = async () => {
    let values: Row;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    setSaving(true);
    try {
      if (editing) await onUpdate(Number(editing.id), values);
      else await onCreate(values);
      message.success(editing ? '已保存' : '已新增');
      setOpen(false);
      await load();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (row: Row) => {
    try {
      await onDelete(Number(row.id));
      message.success(`已${deleteText}`);
      await load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const columns: ColumnsType<Row> = [
    ...fields.map((f) => ({
      title: f.label,
      dataIndex: f.name,
      key: f.name,
      width: f.width ?? (f.type === 'textarea' ? 280 : 130),
      ellipsis: f.ellipsis ?? f.type === 'textarea',
      render: (v: unknown) => {
        if (f.type === 'switch') {
          return v ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>;
        }
        if (f.type === 'tags') {
          return Array.isArray(v) && v.length
            ? v.slice(0, 3).map((x, i) => <Tag key={i}>{String(x)}</Tag>)
            : <Text type="secondary">—</Text>;
        }
        if (v === null || v === undefined || v === '') {
          return <Text type="secondary">—</Text>;
        }
        return String(v);
      },
    })),
  ];

  if (canEdit) {
    columns.push({
      title: '操作',
      key: '__op',
      width: 140 + (extraActions ? 60 : 0),
      fixed: 'right',
      render: (_: unknown, row: Row) => (
        <Space size={0}>
          <Tooltip title="编辑">
            <Button size="small" type="text" icon={<EditOutlined />} onClick={() => openEdit(row)} />
          </Tooltip>
          <Popconfirm
            title={`确定${deleteText}？`}
            description={deleteText === '删除' ? '删除后不可恢复。' : '停用后不再参与问数。'}
            onConfirm={() => void remove(row)}
          >
            <Tooltip title={deleteText}>
              <Button size="small" type="text" danger icon={<DeleteOutlined />} />
            </Tooltip>
          </Popconfirm>
          {extraActions?.(row, load)}
        </Space>
      ),
    });
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.bar}>
        <span className={styles.title}>{title}</span>
        <Space>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
          {canEdit && (
            <Button size="small" type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              新增
            </Button>
          )}
        </Space>
      </div>

      <Table<Row>
        size="small"
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={rows}
        scroll={{ x: 'max-content', y: 520 }}
        pagination={false}
      />

      <Modal
        open={open}
        title={editing ? `编辑${title}` : `新增${title}`}
        okText="保存"
        cancelText="取消"
        confirmLoading={saving}
        onOk={() => void submit()}
        onCancel={() => setOpen(false)}
        width={620}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          {fields.map((f) => (
            <Form.Item
              key={f.name}
              name={f.name}
              label={f.label}
              extra={f.hint}
              rules={f.required ? [{ required: true, message: `请填写${f.label}` }] : undefined}
              valuePropName={f.type === 'switch' ? 'checked' : 'value'}
            >
              {f.type === 'textarea' ? (
                <Input.TextArea rows={4} />
              ) : f.type === 'number' ? (
                <InputNumber style={{ width: '100%' }} />
              ) : f.type === 'switch' ? (
                <Switch />
              ) : f.type === 'tags' ? (
                <Select mode="tags" placeholder="输入后回车添加" />
              ) : (
                <Input />
              )}
            </Form.Item>
          ))}
        </Form>
      </Modal>
    </div>
  );
}
