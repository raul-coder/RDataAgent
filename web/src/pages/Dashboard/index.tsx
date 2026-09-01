import { useCallback, useEffect, useState } from 'react';
import { Alert, Card, Col, Row, Space, Statistic, Table, Tag, Typography } from 'antd';
import {
  fetchOverview, fetchQaStats, type OverviewData, type QaStats,
} from '@/services/api';
import { useAuthStore } from '@/stores/authStore';

const { Title, Text } = Typography;

interface AchieveRow {
  unit: string;
  biz_goal: number;
  income: number;
  achieve_rate: number | null;
  warning: boolean;
}

const fmt = (v: number) => v.toLocaleString('zh-CN', { maximumFractionDigits: 0 });

/**
 * 系统就绪页：确认「后端连通 → 数据库有数 → 语义层就绪 → 造数结果合理」。
 * 后续迭代首页会替换为经营驾驶舱。
 */
export default function DashboardPage() {
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [stats, setStats] = useState<QaStats | null>(null);
  const [achieve, setAchieve] = useState<AchieveRow[]>([]);
  const [error, setError] = useState('');
  const user = useAuthStore((s) => s.user);
  const canSeeSemantic = user?.perms?.some((p) => p.startsWith('sem:')) ?? false;

  const load = useCallback(async () => {
    try {
      const data = await fetchOverview();
      setOverview(data);
      setError('');
    } catch (err) {
      setError((err as Error).message);
    }
    // 看板失败不影响就绪页（它依赖问数记录，全新库可能还没有数据）
    try {
      setStats(await fetchQaStats(7));
    } catch {
      setStats(null);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <div>
        <Title level={4} style={{ marginBottom: 4 }}>
          你好，{user?.nickname || user?.username}
        </Title>
        <Text type="secondary">
          系统就绪检查 · 数据截止 2026-12-31 · 你的角色：{user?.role_codes?.join('、') || '-'}
        </Text>
      </div>

      {error && <Alert type="error" showIcon message="后端未连通" description={error} />}

      <Row gutter={16}>
        <Col span={6}>
          <Card><Statistic title="商业市场台账" value={overview?.fact_contract ?? 0} suffix="行" valueStyle={{ color: 'var(--primary)' }} /></Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="PPL 明细台账" value={overview?.fact_ppl ?? 0} suffix="行" /></Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="目标台账" value={overview?.fact_goal ?? 0} suffix="行" /></Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="经营单元" value={overview?.dim_unit ?? 0} suffix="个" /></Card>
        </Col>
      </Row>

      {/* 语义层是给维护者看的：普通用户没有 sem 权限，看到这些数量也没有意义，
          而且属于系统内部配置概况，不必对所有人展示 */}
      {canSeeSemantic && (
        <Row gutter={16}>
          <Col span={6}><Card><Statistic title="语义层·指标" value={overview?.sem_metric ?? 0} suffix="个" /></Card></Col>
          <Col span={6}><Card><Statistic title="语义层·维度" value={overview?.sem_dimension ?? 0} suffix="个" /></Card></Col>
          <Col span={6}><Card><Statistic title="语义层·口径规则" value={overview?.sem_rule ?? 0} suffix="条" /></Card></Col>
          <Col span={6}><Card><Statistic title="语义层·Few-shot" value={overview?.sem_fewshot ?? 0} suffix="条" /></Card></Col>
        </Row>
      )}

      <Card
        title="问数运行看板"
        extra={<Text type="secondary">最近 {stats?.days ?? 7} 天</Text>}
      >
        {stats ? (
          <Row gutter={16}>
            <Col span={4}>
              <Statistic title="问数次数" value={stats.total} />
            </Col>
            <Col span={4}>
              <Statistic
                title="成功率"
                value={stats.success_rate}
                suffix="%"
                valueStyle={{ color: stats.success_rate >= 90 ? 'var(--success)' : 'var(--danger)' }}
              />
            </Col>
            <Col span={4}>
              <Statistic title="P50 耗时" value={(stats.p50_ms / 1000).toFixed(1)} suffix="s" />
            </Col>
            <Col span={4}>
              <Statistic
                title="P95 耗时"
                value={(stats.p95_ms / 1000).toFixed(1)}
                suffix="s"
                valueStyle={{ color: stats.p95_ms <= 8000 ? 'var(--success)' : 'var(--warning)' }}
              />
            </Col>
            <Col span={4}>
              <Statistic title="缓存命中" value={stats.cache_hit_rate} suffix="%" />
            </Col>
            <Col span={4}>
              <Statistic title="失败次数" value={stats.failed} />
            </Col>
          </Row>
        ) : (
          <Text type="secondary">暂无问数记录</Text>
        )}
        {stats && stats.p95_ms > 8000 && (
          <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
            P95 超出 NFR 要求的 8s：可运行 <Text code>make warmup-cache</Text> 预热常见问题的缓存。
          </Text>
        )}
      </Card>

      <Card
        title="迭代进度"
        extra={<Tag color="success">I5 增强与打磨（已完成）</Tag>}
      >
        <Space direction="vertical" size={4}>
          <Text type="secondary">✅ I0 地基与造数：数据工厂、语义层、一致性校验</Text>
          <Text type="secondary">✅ I1 登录与权限：JWT + RBAC + 数据权限 + 5 个管理页面</Text>
          <Text type="secondary">✅ I2 问数闭环：AgentRuntime + SSE + 5 步链路 + 表/图/结论</Text>
          <Text type="secondary">✅ I3 多轮与调优：指代消解、自愈重试、50 条 Few-shot</Text>
          <Text type="secondary">✅ I4 配置·反馈·日志：应用/模型配置、回复校对、问数日志、快捷提问</Text>
          <Text type="secondary">✅ I5 增强与打磨：台账查看/导入/导出、语音、语义层管理、缓存与看板</Text>
          <Text type="secondary">⏭ I6 验收与发布：全量回归、安全与性能测试、文档与部署</Text>
        </Space>
      </Card>
    </Space>
  );
}
