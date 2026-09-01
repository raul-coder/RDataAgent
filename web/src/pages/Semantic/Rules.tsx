import { useAuthStore } from '@/stores/authStore';
import * as api from '@/services/api';
import CrudTable, { type Field } from './CrudTable';

const FIELDS: Field[] = [
  {
    name: 'scene', label: '场景', type: 'text', required: true, width: 120,
    hint: 'caliber=口径说明；rewrite=问题改写；其余为自定义场景',
  },
  { name: 'title', label: '规则标题', type: 'text', required: true, width: 180 },
  {
    name: 'content', label: '规则内容', type: 'textarea', required: true, width: 360,
    hint: '会原样注入 Prompt，直接影响模型的输出',
  },
  { name: 'priority', label: '优先级', type: 'number', width: 90, hint: '数值越大越优先' },
  { name: 'enabled', label: '状态', type: 'switch', width: 80 },
];

/** 口径规则：注入 Prompt 的业务约定，如「完成率 = 收入 / 目标」 */
export default function Rules() {
  const { user } = useAuthStore();
  return (
    <CrudTable
      title="口径规则"
      fields={FIELDS}
      canEdit={Boolean(user?.perms?.includes('sem:rule:edit'))}
      fetchAll={() => api.fetchSemRules()}
      onCreate={(v) => api.createSemRule(v as api.SemRulePayload)}
      onUpdate={(id, v) => api.updateSemRule(id, v as api.SemRulePayload)}
      onDelete={(id) => api.deleteSemRule(id)}
    />
  );
}
