import { useAuthStore } from '@/stores/authStore';
import * as api from '@/services/api';
import CrudTable, { type Field } from './CrudTable';

const FIELDS: Field[] = [
  { name: 'code', label: '指标代码', type: 'text', required: true, width: 130 },
  { name: 'name', label: '指标名称', type: 'text', required: true, width: 130 },
  { name: 'aliases', label: '同义词', type: 'tags', width: 170, hint: '用户输入这些词时也视为该指标' },
  {
    name: 'expr_sql', label: '计算表达式', type: 'textarea', required: true, width: 300,
    hint: 'SQL 片段，如 SUM(f.year_income)',
  },
  { name: 'unit', label: '单位', type: 'text', width: 80 },
  { name: 'agg_default', label: '默认聚合', type: 'text', width: 90 },
  { name: 'source_id', label: '数据源 ID', type: 'number', required: true, width: 100 },
  { name: 'caliber', label: '口径说明', type: 'text', width: 180 },
  { name: 'enabled', label: '状态', type: 'switch', width: 80 },
];

/** 指标管理：指标口径直接决定问数结果，改动即时生效 */
export default function Metrics() {
  const { user } = useAuthStore();
  return (
    <CrudTable
      title="指标"
      fields={FIELDS}
      canEdit={Boolean(user?.perms?.includes('sem:metric:edit'))}
      fetchAll={() => api.fetchSemMetrics()}
      onCreate={(v) => api.createSemMetric(v as api.SemMetricPayload)}
      onUpdate={(id, v) => api.updateSemMetric(id, v as api.SemMetricPayload)}
      onDelete={(id) => api.deleteSemMetric(id)}
    />
  );
}
