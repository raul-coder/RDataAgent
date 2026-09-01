import { useAuthStore } from '@/stores/authStore';
import * as api from '@/services/api';
import CrudTable, { type Field } from './CrudTable';

const FIELDS: Field[] = [
  { name: 'code', label: '维度代码', type: 'text', required: true, width: 130 },
  { name: 'name', label: '维度名称', type: 'text', required: true, width: 130 },
  { name: 'aliases', label: '同义词', type: 'tags', width: 170 },
  {
    name: 'expr_sql', label: '取值表达式', type: 'textarea', required: true, width: 260,
    hint: '如 d.unit_name',
  },
  {
    name: 'join_sql', label: '关联语句', type: 'textarea', width: 260,
    hint: '如 LEFT JOIN bi.dim_unit d ON d.unit_code = f.unit_code',
  },
  { name: 'dim_type', label: '类型', type: 'text', width: 100 },
  { name: 'source_id', label: '数据源 ID', type: 'number', required: true, width: 100 },
  { name: 'enabled', label: '状态', type: 'switch', width: 80 },
];

/** 维度管理：维度的 JOIN 与取值决定「按什么分组」能否正确生成 */
export default function Dimensions() {
  const { user } = useAuthStore();
  return (
    <CrudTable
      title="维度"
      fields={FIELDS}
      canEdit={Boolean(user?.perms?.includes('sem:dimension:edit'))}
      fetchAll={() => api.fetchSemDimensions()}
      onCreate={(v) => api.createSemDimension(v as api.SemDimensionPayload)}
      onUpdate={(id, v) => api.updateSemDimension(id, v as api.SemDimensionPayload)}
      onDelete={(id) => api.deleteSemDimension(id)}
    />
  );
}
