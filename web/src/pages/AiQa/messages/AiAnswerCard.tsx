import ReactECharts from 'echarts-for-react';
import { Button, Space, Table, Tag, Tooltip, Typography } from 'antd';
import {
  CheckOutlined, CopyOutlined, DatabaseOutlined, WarningOutlined,
  LikeOutlined, LikeFilled, DislikeOutlined, DislikeFilled,
  PauseOutlined, SoundOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useState } from 'react';
import type { ChatMessage } from '@/stores/chatStore';
import { useAppConfig } from '@/hooks/useAppConfig';
import { useSpeechSynthesis } from '@/hooks/useSpeech';
import ThoughtSteps from './ThoughtSteps';
import ClarifyCard from './ClarifyCard';
import ResultToolbar from './ResultToolbar';
import styles from './AiAnswerCard.module.css';

const { Text } = Typography;

interface Props {
  message: ChatMessage;
  onRegenerate?: () => void;
  onDataError?: () => void;
  onRate?: (rating: 'up' | 'down') => void;
  onFollowup?: (q: string) => void;
  onSort?: (dir: 'asc' | 'desc') => void;
  onChart?: (type: 'pie' | 'bar' | 'line') => void;
  onExport?: () => void;
}

export default function AiAnswerCard({
  message, onRegenerate, onDataError, onRate, onFollowup, onSort, onChart, onExport,
}: Props) {
  const [copied, setCopied] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const [rating, setRating] = useState<'up' | 'down' | null>(null);
  const cfg = useAppConfig();
  // 语音朗读（FR-V2）：结论文本可能较长，hook 内部按句切分逐句朗读
  const tts = useSpeechSynthesis();

  const rate = (r: 'up' | 'down') => {
    // 再次点击同向按钮表示取消，此时不调接口（前端只保留"最新态度"语义）
    const next = rating === r ? null : r;
    setRating(next);
    if (next) onRate?.(next);
  };
  const p = message.payload;
  const steps = p?.steps ?? [];
  const table = p?.tables?.[0];
  const chart = p?.charts?.[0];

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message.content || '');
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 忽略剪贴板权限错误 */
    }
  };

  return (
    <div className={styles.card}>
      {p?.clarify?.options?.length ? (
        <ClarifyCard
          options={p.clarify.options}
          reason={p.clarify.reason}
          onPick={(o) => onFollowup?.(o)}
        />
      ) : null}

      {p?.rewritten && p.rewritten !== message.content && (
        <div className={styles.context}>
          <Text type="secondary" style={{ fontSize: 13 }}>
            已结合上文理解为：{p.rewritten}
          </Text>
        </div>
      )}

      {steps.length > 0 && <ThoughtSteps steps={steps} />}

      {p?.degraded && (
        <div className={styles.degraded}>
          <WarningOutlined /> 当前使用降级模式（模型不可用或样本命中），结论为程序化摘要
        </div>
      )}

      {p?.sql && (
        <div className={styles.sqlBar}>
          <Button
            type="text"
            size="small"
            icon={<DatabaseOutlined />}
            onClick={() => setShowSql(!showSql)}
          >
            {showSql ? '隐藏 SQL' : '查看 SQL'}
          </Button>
          {p.data_sources?.length ? (
            <Text type="secondary" style={{ fontSize: 13 }}>
              数据源：{p.data_sources.slice(0, 3).join('、')}
              {p.data_sources.length > 3 ? ` 等 ${p.data_sources.length} 个` : ''}
            </Text>
          ) : null}
        </div>
      )}

      {showSql && p?.sql && <pre className={styles.sql}>{p.sql}</pre>}

      {message.content && (
        <div className={styles.markdown}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
        </div>
      )}

      {message.streaming && !message.content && (
        <div className={styles.thinking}>
          <span className={styles.dot} />
          <span className={styles.dot} />
          <span className={styles.dot} />
          <Text type="secondary"> 正在生成结论…</Text>
        </div>
      )}

      {chart && chart.option && Object.keys(chart.option).length > 0 && (
        <div className={styles.chart}>
          {chart.type === 'metric' ? (
            <div className={styles.metric}>
              <div className={styles.metricLabel}>{chart.option.label}</div>
              <div className={styles.metricValue}>
                {typeof chart.option.value === 'number'
                  ? chart.option.value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
                  : chart.option.value}
              </div>
            </div>
          ) : (
            <ReactECharts option={chart.option} style={{ height: 280 }} notMerge />
          )}
        </div>
      )}

      {table && table.columns?.length > 0 && (
        <div className={styles.table}>
          <Table
            size="small"
            rowKey={(_, i) => String(i)}
            dataSource={table.rows.map((r, i) => ({
              key: i,
              ...Object.fromEntries(table.columns.map((c, j) => [c, r[j]])),
            }))}
            scroll={{ x: true }}
            pagination={table.rows.length > 12 ? { pageSize: 12, size: 'small' } : false}
            columns={table.columns.map((c) => ({
              title: c,
              dataIndex: c,
              render: (v: any) =>
                typeof v === 'number'
                  ? v.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
                  : v ?? '-',
            }))}
          />
        </div>
      )}

      {message.error && (
        <div className={styles.error}>
          <WarningOutlined /> {message.error}
        </div>
      )}

      {table && table.columns?.length > 0 && !message.streaming && (
        <ResultToolbar
          disabled={false}
          onSort={(dir) => onSort?.(dir)}
          onChart={(t) => onChart?.(t)}
          onExport={() => onExport?.()}
        />
      )}

      {!message.streaming && (
        <div className={styles.actions}>
          <Tooltip title="复制">
            <Button type="text" size="small" icon={copied ? <CheckOutlined /> : <CopyOutlined />} onClick={copy} />
          </Tooltip>
          {cfg?.tts && (
            <Space size={0}>
              <Tooltip
                title={
                  !tts.supported
                    ? '当前浏览器不支持语音播放（建议使用 Chrome）'
                    : tts.speaking
                      ? (tts.paused ? '继续朗读' : '暂停朗读')
                      : '朗读回答'
                }
              >
                <Button
                  type="text"
                  size="small"
                  icon={tts.speaking && !tts.paused ? <PauseOutlined /> : <SoundOutlined />}
                  disabled={!tts.supported}
                  onClick={() => (tts.speaking ? tts.togglePause() : tts.speak(message.content))}
                />
              </Tooltip>
              {tts.speaking && (
                <Button type="text" size="small" onClick={tts.stop}>
                  停止
                </Button>
              )}
            </Space>
          )}
          {onRegenerate && (
            <Button type="text" size="small" onClick={onRegenerate}>
              重新生成
            </Button>
          )}
          {onRate && (
            <>
              <Tooltip title={rating === 'up' ? '取消点赞' : '有帮助'}>
                <Button
                  type="text"
                  size="small"
                  icon={rating === 'up' ? <LikeFilled /> : <LikeOutlined />}
                  onClick={() => rate('up')}
                />
              </Tooltip>
              <Tooltip title={rating === 'down' ? '取消点踩' : '没帮助'}>
                <Button
                  type="text"
                  size="small"
                  icon={rating === 'down' ? <DislikeFilled /> : <DislikeOutlined />}
                  onClick={() => rate('down')}
                />
              </Tooltip>
            </>
          )}
          {onDataError && (
            <Button type="text" size="small" onClick={onDataError}>
              数据有误，提交反馈
            </Button>
          )}
        </div>
      )}

      {p?.followups?.length ? (
        <div className={styles.followups}>
          {p.followups.map((q) => (
            <span key={q} className={styles.chip} onClick={() => onFollowup?.(q)}>
              {q}
              <span className={styles.chipArrow}>→</span>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
