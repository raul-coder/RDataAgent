import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  App, Button, Card, Checkbox, Col, Divider, Row, Select, Space, Table, Tag, Tree, Typography,
} from 'antd';
import type { DataNode } from 'antd/es/tree';
import { SaveOutlined } from '@ant-design/icons';
import { useSearchParams } from 'react-router-dom';
import * as api from '@/services/api';

const { Text, Title } = Typography;

/** 权限配置：菜单权限树 + 操作权限 + 数据权限（经营单元可见范围） */
export default function PermissionConfig() {
  const { message } = App.useApp();
  const [params, setParams] = useSearchParams();
  const roleId = Number(params.get('roleId') || 0);

  const [roles, setRoles] = useState<api.RoleItem[]>([]);
  const [tree, setTree] = useState<api.MenuTreeNode[]>([]);
  const [units, setUnits] = useState<api.UnitOption[]>([]);
  const [checkedKeys, setCheckedKeys] = useState<number[]>([]);
  const [opsMap, setOpsMap] = useState<Record<number, string[]>>({});
  const [dataPerms, setDataPerms] = useState<Record<number, string[]>>({});
  const [selectedMenu, setSelectedMenu] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.fetchRoles('', 1, 100).then((r) => setRoles(r.items)).catch(() => setRoles([]));
    api.fetchUnits().then(setUnits).catch(() => setUnits([]));
  }, []);

  const load = useCallback(
    async (rid: number) => {
      if (!rid) { setTree([]); return; }
      try {
        const res = await api.fetchRolePermissions(rid);
        setTree(res.menus);
        const checked: number[] = [];
        const ops: Record<number, string[]> = {};
        const walk = (nodes: api.MenuTreeNode[]) => {
          nodes.forEach((n) => {
            if (n.checked) checked.push(n.id);
            ops[n.id] = n.ops ?? [];
            if (n.children?.length) walk(n.children);
          });
        };
        walk(res.menus);
        setCheckedKeys(checked);
        setOpsMap(ops);
        const dp: Record<number, string[]> = {};
        (res.data_perms ?? []).forEach((d) => { dp[d.menu_id] = d.unit_codes ?? []; });
        setDataPerms(dp);
      } catch (e) {
        message.error((e as Error).message);
      }
    },
    [message],
  );

  useEffect(() => {
    if (roles.length && !roleId) {
      const first = roles[0];
      if (first) setParams({ roleId: String(first.id) }, { replace: true });
    }
  }, [roles, roleId, setParams]);

  useEffect(() => { void load(roleId); }, [roleId, load]);

  const treeData = useMemo<DataNode[]>(() => {
    const convert = (nodes: api.MenuTreeNode[]): DataNode[] =>
      nodes.map((n) => ({
        key: n.id,
        title: (
          <Space size={6}>
            <span>{n.name}</span>
            {n.perm_code && <Text type="secondary" style={{ fontSize: 12 }}>{n.perm_code}</Text>}
            {n.type === 'M' && <Tag>目录</Tag>}
          </Space>
        ),
        children: n.children?.length ? convert(n.children) : undefined,
      }));
    return convert(tree);
  }, [tree]);

  /** 数据权限只对「叶子菜单」配置 */
  const leafMenus = useMemo(() => {
    const out: api.MenuTreeNode[] = [];
    const walk = (nodes: api.MenuTreeNode[]) => {
      nodes.forEach((n) => {
        if (n.children?.length) walk(n.children);
        else if (n.type === 'C') out.push(n);
      });
    };
    walk(tree);
    return out;
  }, [tree]);

  const save = async () => {
    if (!roleId) return;
    setSaving(true);
    try {
      await api.saveRolePermissions(roleId, {
        menus: checkedKeys.map((id) => ({ id, checked: true, ops: opsMap[id] ?? ['view'] })),
        data_perms: leafMenus.map((m) => ({ menu_id: m.id, unit_codes: dataPerms[m.id] ?? [] })),
      });
      message.success('权限已保存，相关用户下次请求时生效');
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      title="权限配置"
      extra={
        <Space>
          <Text type="secondary">角色</Text>
          <Select
            style={{ width: 200 }}
            value={roleId || undefined}
            onChange={(v) => setParams({ roleId: String(v) })}
            options={roles.map((r) => ({ value: r.id, label: `${r.name}（${r.user_count} 人）` }))}
          />
          <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={save}>
            保存配置
          </Button>
        </Space>
      }
    >
      <Row gutter={24}>
        <Col span={12}>
          <Title level={5}>菜单权限</Title>
          <div style={{ maxHeight: 420, overflow: 'auto', border: '1px solid var(--border)', borderRadius: 8, padding: 8 }}>
            <Tree
              checkable
              selectable
              treeData={treeData}
              checkedKeys={checkedKeys}
              onCheck={(keys) => setCheckedKeys((keys as React.Key[]).map(Number))}
              onSelect={(keys) => setSelectedMenu(keys.length ? Number(keys[0]) : null)}
            />
          </div>
        </Col>

        <Col span={12}>
          <Title level={5}>操作权限</Title>
          {selectedMenu ? (
            <>
              <Text type="secondary">
                当前菜单：{tree.find((t) => t.id === selectedMenu)?.name ?? `#${selectedMenu}`}
              </Text>
              <div style={{ marginTop: 12 }}>
                <Checkbox.Group
                  options={api.OPERATIONS.map(([value, label]) => ({ value, label }))}
                  value={opsMap[selectedMenu] ?? []}
                  onChange={(v) =>
                    setOpsMap((m) => ({ ...m, [selectedMenu]: v as string[] }))
                  }
                />
              </div>
              <div style={{ marginTop: 8 }}>
                <Button
                  size="small"
                  onClick={() => setOpsMap((m) => ({ ...m, [selectedMenu]: api.OPERATIONS.map(([v]) => v) }))}
                >
                  全选
                </Button>
                <Button
                  size="small"
                  style={{ marginLeft: 8 }}
                  onClick={() => setOpsMap((m) => ({ ...m, [selectedMenu]: ['view'] }))}
                >
                  只读
                </Button>
              </div>
            </>
          ) : (
            <Text type="secondary">请在左侧选择一个菜单以配置其操作权限</Text>
          )}
        </Col>
      </Row>

      <Divider />

      <Title level={5}>
        数据权限
        <Text type="secondary" style={{ fontSize: 13, fontWeight: 400, marginLeft: 8 }}>
          按经营单元控制可见范围；留空表示不限制
        </Text>
      </Title>
      <Table
        size="middle"
        rowKey="id"
        pagination={false}
        scroll={{ x: 720 }}
        dataSource={leafMenus}
        columns={[
          { title: '菜单', dataIndex: 'name', width: 160 },
          {
            title: '可见经营单元',
            render: (_, row) => (
              <Select
                mode="multiple"
                allowClear
                style={{ width: '100%' }}
                placeholder="不限制（可查看全部经营单元）"
                value={dataPerms[row.id] ?? []}
                onChange={(v) => setDataPerms((m) => ({ ...m, [row.id]: v as string[] }))}
                options={units.map((u) => ({ value: u.code, label: `${u.name}（${u.region}）` }))}
                maxTagCount={8}
              />
            ),
          },
        ]}
      />
    </Card>
  );
}
