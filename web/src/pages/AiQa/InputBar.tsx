import { useEffect, useState } from 'react';
import { App, Button, Checkbox, Input, Popover, Space, Tooltip } from 'antd';
import { AudioOutlined, DatabaseOutlined, SendOutlined } from '@ant-design/icons';
import { fetchDataSources, type DataSourceItem } from '@/services/dataSource';
import { useAppConfig } from '@/hooks/useAppConfig';
import { useSpeechRecognition } from '@/hooks/useSpeech';
import QuickPanel from './QuickPanel';
import styles from './InputBar.module.css';

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  loading?: boolean;
  sourceIds: number[];
  onSourceChange: (ids: number[]) => void;
}

/** 输入区：数据源选择 + 输入框 + 发送（对齐 demo 的 qa-input-bar） */
export default function InputBar({
  value, onChange, onSend, loading, sourceIds, onSourceChange,
}: Props) {
  const { message } = App.useApp();
  const [sources, setSources] = useState<DataSourceItem[]>([]);
  const [open, setOpen] = useState(false);
  const cfg = useAppConfig();

  // 语音输入（FR-V1）：识别结果追加到已有内容之后，
  // 而不是覆盖——用户可能先打了半句话再开口。
  const { supported: sttOk, listening, toggle: toggleMic } = useSpeechRecognition({
    onResult: (text) => onChange(value ? `${value}${text}` : text),
    onError: (m) => message.error(m),
  });

  useEffect(() => {
    fetchDataSources().then(setSources).catch(() => setSources([]));
  }, []);

  const groups: Record<string, DataSourceItem[]> = {};
  sources.forEach((s) => {
    (groups[s.group_name] ||= []).push(s);
  });

  const checkedCount = sourceIds.length || sources.length;
  const label =
    sourceIds.length === 0 || checkedCount === sources.length
      ? '已选择所有数据源'
      : `已选 ${checkedCount} 个数据源`;

  const toggle = (id: number, checked: boolean) => {
    const base = sourceIds.length ? sourceIds : sources.map((s) => s.id);
    const next = checked ? [...new Set([...base, id])] : base.filter((x) => x !== id);
    onSourceChange(next);
  };

  return (
    <div className={styles.wrap}>
      <div className={styles.bar}>
        <QuickPanel onPick={onChange} currentQuestion={value} />

        <Popover
          open={open}
          onOpenChange={setOpen}
          trigger="click"
          placement="topLeft"
          title="选择数据源"
          content={
            <div className={styles.popover}>
              {Object.entries(groups).map(([g, items]) => (
                <div key={g} className={styles.group}>
                  <div className={styles.groupTitle}>{g}</div>
                  <Space direction="vertical" size={2}>
                    {items.map((s) => (
                      <Checkbox
                        key={s.id}
                        checked={
                          sourceIds.length === 0 ? true : sourceIds.includes(s.id)
                        }
                        onChange={(e) => toggle(s.id, e.target.checked)}
                      >
                        {s.name}
                      </Checkbox>
                    ))}
                  </Space>
                </div>
              ))}
              <div className={styles.popFooter}>
                <Button size="small" onClick={() => onSourceChange([])}>
                  全部
                </Button>
                <Button size="small" onClick={() => onSourceChange([])}>
                  重置
                </Button>
              </div>
            </div>
          }
        >
          <Button icon={<DatabaseOutlined />} size="large" className={styles.sourceBtn}>
            {label}
          </Button>
        </Popover>

        <Input.TextArea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoSize={{ minRows: 1, maxRows: 5 }}
          placeholder="请写下您的想法…（Enter 发送，Shift+Enter 换行）"
          className={styles.input}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault();
              if (value.trim() && !loading) onSend();
            }
          }}
        />

        {cfg?.stt && (
          <Tooltip
            title={
              !sttOk
                ? '当前浏览器不支持语音输入（建议使用 Chrome）'
                : listening
                  ? '停止录音'
                  : '语音输入'
            }
          >
            <Button
              size="large"
              icon={<AudioOutlined />}
              danger={listening}
              disabled={!sttOk}
              onClick={toggleMic}
            />
          </Tooltip>
        )}

        <Tooltip title="发送">
          <Button
            type="primary"
            size="large"
            icon={<SendOutlined />}
            loading={loading}
            disabled={!value.trim()}
            onClick={onSend}
            className={styles.send}
          />
        </Tooltip>
      </div>
      <div className={styles.hint}>
        数据截止 2026-12-31 · 支持排名 / 趋势 / 占比 / 同比 / 风险预警等经营问题
      </div>
    </div>
  );
}
