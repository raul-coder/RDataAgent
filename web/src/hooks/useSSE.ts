import { useCallback, useRef } from 'react';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import { tokenStore, onSessionExpired } from '@/services/http';

export interface SSEPayload {
  event: string;
  data: any;
}

interface UseSSEOptions {
  onEvent: (payload: SSEPayload) => void;
  onError?: (err: Error) => void;
}

/**
 * SSE 客户端（POST + 自定义 header，原生 EventSource 不支持）。
 * 返回 send / abort。
 */
export function useSSE({ onEvent, onError }: UseSSEOptions) {
  const ctrlRef = useRef<AbortController | null>(null);

  const abort = useCallback(() => {
    ctrlRef.current?.abort();
    ctrlRef.current = null;
  }, []);

  const send = useCallback(
    async (url: string, body: unknown) => {
      abort();
      const ctrl = new AbortController();
      ctrlRef.current = ctrl;

      try {
        await fetchEventSource(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${tokenStore.access}`,
          },
          body: JSON.stringify(body),
          signal: ctrl.signal,
          openWhenHidden: true,
          async onopen(resp) {
            if (resp.status === 401) {
              onSessionExpired();
              throw new Error('登录已过期，请重新登录');
            }
            if (!resp.ok) {
              throw new Error(`服务返回 ${resp.status}`);
            }
          },
          onmessage(msg) {
            if (!msg.event) return;
            try {
              onEvent({ event: msg.event, data: JSON.parse(msg.data) });
            } catch {
              onEvent({ event: msg.event, data: msg.data });
            }
          },
          onerror(err) {
            throw err;
          },
        });
      } catch (err) {
        if ((err as Error).name === 'AbortError') return;
        onError?.(err as Error);
      } finally {
        if (ctrlRef.current === ctrl) ctrlRef.current = null;
      }
    },
    [onEvent, onError, abort],
  );

  return { send, abort };
}
