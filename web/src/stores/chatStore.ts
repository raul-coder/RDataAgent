import { create } from 'zustand';
import type { MenuTreeNode } from '@/services/api';

export interface ThoughtStep {
  index: number;
  title: string;
  status: 'pending' | 'running' | 'done' | 'fail';
  desc?: string;
  cost_ms?: number;
}

export interface TableBlock {
  columns: string[];
  rows: any[][];
  total: number;
  truncated: boolean;
}

export interface ChartBlock {
  type: string;
  option: any;
}

export interface ChatMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  /** 流式进行中 */
  streaming?: boolean;
  payload?: {
    steps: ThoughtStep[];
    sql: string;
    data_sources: string[];
    tables: TableBlock[];
    charts: ChartBlock[];
    followups: string[];
    degraded?: boolean;
    /** 多轮：改写后的完整问题 */
    rewritten?: string;
    /** 多轮：本轮生效的槽位 */
    slots?: Record<string, any>;
    /** 需要澄清时的候选 */
    clarify?: { question: string; options: string[]; reason: string };
  };
  error?: string;
  created_at?: string;
}

interface SessionBrief {
  id: number;
  title: string;
  pinned: boolean;
  msg_count: number;
  last_msg_at: string;
  user_feedback?: string;
  admin_feedback?: string;
}

interface ChatState {
  sessions: SessionBrief[];
  currentId: number | null;
  messages: Record<number, ChatMessage[]>;
  streaming: boolean;
  sourceIds: number[];
  sources: MenuTreeNode[] | { id: number; name: string; group_name: string }[];

  setSessions: (s: SessionBrief[]) => void;
  setCurrent: (id: number | null) => void;
  setMessages: (id: number, msgs: ChatMessage[]) => void;
  appendMessage: (id: number, msg: ChatMessage) => void;
  updateLastAssistant: (id: number, patch: Partial<ChatMessage>) => void;
  appendContent: (id: number, delta: string) => void;
  patchLastPayload: (id: number, patch: Record<string, unknown>) => void;
  setStreaming: (v: boolean) => void;
  clearMessages: (id: number) => void;
  setSourceIds: (ids: number[]) => void;
  setSources: (s: any[]) => void;
}

export const useChatStore = create<ChatState>((set) => ({
  sessions: [],
  currentId: null,
  messages: {},
  streaming: false,
  sourceIds: [],
  sources: [],

  setSessions: (sessions) => set({ sessions }),
  setCurrent: (currentId) => set({ currentId }),
  setMessages: (id, msgs) =>
    set((s) => ({ messages: { ...s.messages, [id]: msgs } })),
  appendMessage: (id, msg) =>
    set((s) => ({ messages: { ...s.messages, [id]: [...(s.messages[id] ?? []), msg] } })),
  updateLastAssistant: (id, patch) =>
    set((s) => {
      const list = [...(s.messages[id] ?? [])];
      for (let i = list.length - 1; i >= 0; i -= 1) {
        if (list[i].role === 'assistant') {
          list[i] = { ...list[i], ...patch };
          break;
        }
      }
      return { messages: { ...s.messages, [id]: list } };
    }),
  appendContent: (id, delta) =>
    set((s) => {
      const list = [...(s.messages[id] ?? [])];
      for (let i = list.length - 1; i >= 0; i -= 1) {
        if (list[i].role === 'assistant') {
          list[i] = { ...list[i], content: (list[i].content ?? '') + delta };
          break;
        }
      }
      return { messages: { ...s.messages, [id]: list } };
    }),
  patchLastPayload: (id, patch) =>
    set((s) => {
      const list = [...(s.messages[id] ?? [])];
      for (let i = list.length - 1; i >= 0; i -= 1) {
        if (list[i].role === 'assistant') {
          list[i] = {
            ...list[i],
            payload: { steps: [], sql: '', data_sources: [], tables: [], charts: [], followups: [], ...(list[i].payload ?? {}), ...patch },
          } as ChatMessage;
          break;
        }
      }
      return { messages: { ...s.messages, [id]: list } };
    }),
  setStreaming: (streaming) => set({ streaming }),
  clearMessages: (id) => set((s) => ({ messages: { ...s.messages, [id]: [] } })),
  setSourceIds: (sourceIds) => set({ sourceIds }),
  setSources: (sources) => set({ sources }),
}));
