import { useState } from 'react';
import { App, Button, Tooltip } from 'antd';
import { CheckCircleOutlined } from '@ant-design/icons';
import { useAuthStore } from '@/stores/authStore';
import * as api from '@/services/api';
import CrudTable, { type Field } from './CrudTable';

const FIELDS: Field[] = [
  { name: 'question', label: '示例问题', type: 'text', required: true, width: 220 },
  {
    name: 'sql', label: '标准 SQL', type: 'textarea', required: true, width: 380,
    hint: '作为 Few-shot 注入 Prompt，语法必须可执行',
  },
  { name: 'rewritten', label: '改写后问题', type: 'text', width: 200 },
  { name: 'notes', label: '备注', type: 'text', width: 140 },
  { name: 'hit_count', label: '命中次数', type: 'number', width: 90 },
  { name: 'verified', label: '已验证', type: 'switch', width: 80 },
];

/**
 * Few-shot 样本管理。
 * 与其余三类不同，这里额外提供「验证 SQL」：表结构变更后历史样本可能失效，
 * 失效样本混在 Prompt 里会误导模型，所以改完顺手点一下验证很有必要。
 */
export default function Fewshots() {
  const { message } = App.useApp();
  const { user } = useAuthStore();
  const [verifying, setVerifying] = useState<number | null>(null);

  return (
    <CrudTable
      title="Few-shot 样本"
      fields={FIELDS}
      canEdit={Boolean(user?.perms?.includes('sem:fewshot:edit'))}
      deleteText="删除"
      fetchAll={async () => (await api.fetchSemFewshots()).items}
      onCreate={(v) => api.createSemFewshot(v as api.SemFewshotPayload)}
      onUpdate={(id, v) => api.updateSemFewshot(id, v as api.SemFewshotPayload)}
      onDelete={(id) => api.deleteSemFewshot(id)}
      extraActions={(row) => (
        <Tooltip title="真跑一遍 SQL，验证是否可执行">
          <Button
            size="small"
            type="text"
            icon={<CheckCircleOutlined />}
            loading={verifying === Number(row.id)}
            onClick={async () => {
              const id = Number(row.id);
              setVerifying(id);
              try {
                const r = await api.verifySemFewshot(id);
                if (r.ok) message.success(`SQL 可执行，返回 ${r.rows} 行`);
                else message.error(`执行失败：${r.error ?? '未知错误'}`);
              } catch (e) {
                message.error((e as Error).message);
              } finally {
                setVerifying(null);
              }
            }}
          />
        </Tooltip>
      )}
    />
  );
}
