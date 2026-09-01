import { useCallback, useEffect, useRef, useState } from 'react';
import { App, Button, Space, Typography } from 'antd';
import { DownOutlined, RobotOutlined } from '@ant-design/icons';
import { useSearchParams } from 'react-router-dom';
import { useSSE } from '@/hooks/useSSE';
import { useChatStore, type ChatMessage, type ThoughtStep } from '@/stores/chatStore';
import * as api from '@/services/api';

import SessionList, { type SessionBrief } from './SessionList';
import InputBar from './InputBar';
import AiAnswerCard from './messages/AiAnswerCard';
import UserMessage from './messages/UserMessage';
import styles from './index.module.css';

const { Text } = Typography;

const CHART_LABEL: Record<string, string> = { pie: '饼', bar: '柱状', line: '折线' };

/** 结果集导出为 CSV（UTF-8 BOM，Excel 可直接打开） */
function exportCsv(m: ChatMessage) {
  const table = m.payload?.tables?.[0];
  if (!table) return;
  const esc = (v: any) => {
    const s = v === null || v === undefined ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv =
    '﻿' +
    [table.columns.map(esc).join(','), ...table.rows.map((r) => r.map(esc).join(','))].join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `问数结果_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

/** 接口不可用时的后备快捷提问，避免欢迎页留白 */
const FALLBACK_QUICK = [
  '2026年各经营单元收入排名',
  '北京代表处今年达成情况',
  '各产品线收入占比',
  '2026年每月的合同金额趋势',
  '高风险项目有哪些',
  '完成率低于60%的预警单元',
];

export default function AiQaPage() {
  const { message } = App.useApp();
  const [searchParams, setSearchParams] = useSearchParams();
  // 开场白与快捷提问改由「应用配置」驱动（FR-S1），接口失败时回退到内置默认值
  const [greeting, setGreeting] = useState('');
  const [quickQuestions, setQuickQuestions] = useState<string[]>(FALLBACK_QUICK);
  const {
    sessions, setSessions, currentId, setCurrent,
    messages, setMessages, appendMessage, updateLastAssistant,
    appendContent, patchLastPayload,
    streaming, setStreaming, sourceIds, setSourceIds,
  } = useChatStore();

  // 把事件补丁合并进「最后一条助手消息」的 payload
  const patchPayload = (sid: number, patch: Record<string, unknown>) =>
    patchLastPayload(sid, patch);

  const [input, setInput] = useState('');
  const [keyword, setKeyword] = useState('');
  const list = currentId ? messages[currentId] ?? [] : [];

  // 流式 token 缓冲：高频 token 事件按 80ms 批量 flush，避免重渲染风暴
  const buffer = useRef('');
  const timer = useRef<number | null>(null);

  // ── 自动滚动到底部 ──────────────────────────────────────────────
  // 默认跟随最新内容。但用户手动往上翻时要**暂停跟随**——
  // 否则想看历史回答的人会被流式输出不断拽回底部，根本看不成。
  const messagesRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const [showBackToBottom, setShowBackToBottom] = useState(false);

  const scrollToBottom = useCallback((smooth = false) => {
    const el = messagesRef.current;
    if (!el) return;
    // 必须在下一帧再滚：调用方往往是刚 append 完内容，此刻 DOM 还没重排，
    // 直接滚只会滚到「上一帧的底部」，流式输出时会越差越多。
    requestAnimationFrame(() => {
      el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
    });
  }, []);

  const handleScroll = useCallback(() => {
    const el = messagesRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottom.current = distance < 80;
    setShowBackToBottom(distance > 300);
  }, []);

  // 切换会话、新增消息时回到最底部（新的一轮，重新跟随）
  useEffect(() => {
    stickToBottom.current = true;
    setShowBackToBottom(false);
    scrollToBottom();
  }, [currentId, list.length, scrollToBottom]);

  const flush = useCallback(
    (sid: number) => {
      timer.current = null;
      if (!buffer.current) return;
      const delta = buffer.current;
      buffer.current = '';
      appendContent(sid, delta);
      // 流式输出让内容持续变高，跟着往下滚
      if (stickToBottom.current) scrollToBottom();
    },
    [appendContent, scrollToBottom],
  );

  const loadSessions = useCallback(
    async (kw = '') => {
      try {
        const res = await api.fetchSessions(kw);
        setSessions(res.items as SessionBrief[]);
      } catch (e) {
        message.error((e as Error).message);
      }
    },
    [setSessions, message],
  );

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  // 应用配置驱动欢迎页：开场白开关 + 文案 + 快捷提问（推荐/常问）
  useEffect(() => {
    (async () => {
      try {
        const cfg = await api.fetchAppConfig();
        setGreeting(cfg.greeting ? (cfg.greetingText || '') : '');
        const tabs = (await api.fetchQuickQuestions()) as api.QuickQuestionTabs;
        const merged = [
          ...(tabs.recommend ?? []).map((q) => q.question),
          ...(tabs.recent ?? []).map((q) => q.question),
        ];
        const uniq = Array.from(new Set(merged));
        if (uniq.length) setQuickQuestions(uniq.slice(0, 8));
      } catch {
        /* 配置接口异常时沿用内置默认值，不影响问数 */
      }
    })();
  }, []);

  const selectSession = async (id: number) => {
    setCurrent(id);
    try {
      const detail = await api.fetchSessionDetail(id);
      const msgs: ChatMessage[] = (detail.messages ?? []).map((m: any) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        payload: m.payload,
        created_at: m.created_at,
      }));
      setMessages(id, msgs);
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  // 从「回复校对」页跳转 ?session=123 时自动打开该会话回放
  useEffect(() => {
    const sid = Number(searchParams.get('session'));
    if (!sid) return;
    void selectSession(sid);
    searchParams.delete('session');
    setSearchParams(searchParams, { replace: true });
    // 仅在挂载时消费一次该参数
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const newSession = () => {
    setCurrent(null);
    setInput('');
  };

  const ensureSession = async (): Promise<number | null> => {
    if (currentId) return currentId;
    const res = await api.createSession('新对话');
    setCurrent(res.id);
    void loadSessions(keyword);
    return res.id;
  };

  const { send, abort } = useSSE({
    onEvent: ({ event, data }) => {
      const sid = useChatStore.getState().currentId;
      if (!sid) return;

      switch (event) {
        case 'meta': {
          // 后端在「用户消息落库」和「AI 回答落库」两个时机会各发一次 meta，
          // 用 role 区分，把真实自增 id 回填到本地临时（负数）id 上。
          // 若不回填，m.id 永远是负数，点赞点踩 / 数据有误会因 id <= 0 被拦掉。
          const role = data?.role;
          const realId = data?.message_id;
          if (!role || !realId) break;
          const cur = useChatStore.getState().messages[sid] ?? [];
          const idx = [...cur].reverse().findIndex((m) => m.role === role);
          if (idx < 0) break;
          const next = [...cur];
          const i = cur.length - 1 - idx;
          if (next[i].id === realId) break;
          next[i] = { ...next[i], id: realId };
          setMessages(sid, next);
          break;
        }

        case 'step': {
          const step = data as ThoughtStep;
          const cur = useChatStore.getState().messages[sid] ?? [];
          const last = [...cur];
          const idx = [...last].reverse().findIndex((m) => m.role === 'assistant');
          if (idx < 0) break;
          const i = last.length - 1 - idx;
          const steps = [...(last[i].payload?.steps ?? [])];
          const pos = steps.findIndex((s) => s.index === step.index);
          if (pos >= 0) steps[pos] = step;
          else steps.push(step);
          steps.sort((a, b) => a.index - b.index);
          updateLastAssistant(sid, {
            payload: { ...(last[i].payload as any), steps },
          });
          break;
        }

        case 'sql':
          patchPayload(sid, { sql: data.sql, data_sources: data.data_sources ?? [] });
          break;

        case 'table':
          patchPayload(sid, { tables: [data] });
          break;

        case 'chart':
          patchPayload(sid, { charts: [{ type: data.type, option: data.option }] });
          break;

        case 'token':
          buffer.current += data.delta ?? '';
          if (timer.current === null) {
            timer.current = window.setTimeout(() => flush(sid), 80);
          }
          break;

        case 'followups':
          patchPayload(sid, { followups: data.items ?? [] });
          break;

        case 'clarify':
          patchPayload(sid, {
            clarify: {
              question: data.question ?? '',
              options: data.options ?? [],
              reason: data.reason ?? '',
            },
          });
          break;

        case 'slots':
          patchPayload(sid, { slots: data.slots ?? {} });
          break;

        case 'result_op':
          // 结果二次加工：后端直接变换缓存结果集，随后会推送新的 table/chart
          patchPayload(sid, { steps: [] });
          break;

        case 'error':
          updateLastAssistant(sid, { error: data.message });
          break;

        case 'done':
          if (timer.current !== null) {
            window.clearTimeout(timer.current);
            flush(sid);
          }
          updateLastAssistant(sid, { streaming: false });
          setStreaming(false);
          void loadSessions(keyword);
          break;
      }
    },
    onError: (err) => {
      message.error(err.message || '问数失败');
      setStreaming(false);
    },
  });

  const doAsk = async (question: string) => {
    if (!question.trim() || streaming) return;
    const sid = await ensureSession();
    if (!sid) return;

    appendMessage(sid, { id: -Date.now(), role: 'user', content: question });
    appendMessage(sid, {
      id: -Date.now() - 1,
      role: 'assistant',
      content: '',
      streaming: true,
      payload: { steps: [], sql: '', data_sources: [], tables: [], charts: [], followups: [] },
    });
    setStreaming(true);
    setInput('');
    stickToBottom.current = true;
    scrollToBottom();
    await send('/api/v1/chat/completions', {
      session_id: sid,
      content: question,
      source_ids: sourceIds.length ? sourceIds : null,
    });
  };

  // 会话操作
  const rename = async (id: number, title: string) => {
    await api.renameSession(id, title);
    void loadSessions(keyword);
  };
  const remove = async (id: number) => {
    await api.deleteSession(id);
    if (currentId === id) setCurrent(null);
    void loadSessions(keyword);
  };
  const pin = async (id: number, pinned: boolean) => {
    await api.pinSession(id, pinned);
    void loadSessions(keyword);
  };

  const handleDataError = async (msgId: number) => {
    try {
      const res = await api.submitDataError(msgId);
      message.success(`反馈已提交（单号 ${res.feedback_id}），我们将尽快核查`);
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const handleRate = async (msgId: number, rating: 'up' | 'down') => {
    try {
      await api.rateMessage(msgId, rating);
      message.success(rating === 'up' ? '感谢反馈' : '已收到，我们会持续改进');
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  return (
    <div className={styles.page}>
      <SessionList
        sessions={sessions}
        currentId={currentId}
        onSelect={selectSession}
        onCreate={newSession}
        onRename={rename}
        onDelete={remove}
        onPin={pin}
        onSearch={(kw) => {
          setKeyword(kw);
          void loadSessions(kw);
        }}
      />

      <div className={styles.main}>
        {list.length === 0 ? (
          <div className={styles.welcome}>
            <RobotOutlined className={styles.logo} />
            <h2 className={styles.title}>智能问数</h2>
            <Text type="secondary" style={{ maxWidth: 560, textAlign: 'center' }}>
              {greeting || '欢迎使用智能AI问数，您可以向我咨询经营数据、报表分析相关问题。'}
            </Text>
            <div className={styles.quick}>
              {quickQuestions.map((q) => (
                <button key={q} className={styles.quickItem} onClick={() => void doAsk(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div
            className={styles.messages}
            ref={messagesRef}
            onScroll={handleScroll}
          >
            {list.map((m, i) =>
              m.role === 'user' ? (
                <UserMessage
                  key={m.id}
                  content={m.content}
                  onEdit={(text) => {
                    // 编辑重发：移除最后一条 AI 回答后重新提问
                    const next = list.slice(0, i).filter((x) => !(x.role === 'assistant' && x.id === list[i + 1]?.id));
                    setMessages(currentId!, [...next, { ...m, content: text }]);
                    void doAsk(text);
                  }}
                />
              ) : (
                <AiAnswerCard
                  key={m.id}
                  message={m}
                  onRegenerate={() => {
                    const prev = [...list].slice(0, i).reverse().find((x) => x.role === 'user');
                    if (prev) {
                      setMessages(currentId!, list.slice(0, i));
                      void doAsk(prev.content);
                    }
                  }}
                  onDataError={() => m.id > 0 && void handleDataError(m.id)}
                  onRate={(r) => m.id > 0 && void handleRate(m.id, r)}
                  onFollowup={(q) => void doAsk(q)}
                  onSort={(dir) => void doAsk(dir === 'desc' ? '按降序排序' : '按升序排序')}
                  onChart={(t) => void doAsk(`换成${CHART_LABEL[t]}图`)}
                  onExport={() => exportCsv(m)}
                />
              ),
            )}
            {streaming && (
              <div className={styles.abortRow}>
                <Button size="small" onClick={abort}>停止生成</Button>
              </div>
            )}
          </div>
        )}

        {/* 用户往上翻看历史时不再强制拉回，给一个回到底部的入口 */}
        {showBackToBottom && (
          <Button
            className={styles.backToBottom}
            shape="circle"
            icon={<DownOutlined />}
            title="回到最新"
            onClick={() => {
              stickToBottom.current = true;
              scrollToBottom(true);
            }}
          />
        )}

        <InputBar
          value={input}
          onChange={setInput}
          onSend={() => void doAsk(input)}
          loading={streaming}
          sourceIds={sourceIds}
          onSourceChange={setSourceIds}
        />
      </div>
    </div>
  );
}
