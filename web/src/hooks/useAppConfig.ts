import { useEffect, useState } from 'react';
import * as api from '@/services/api';

/**
 * 应用配置（模块级缓存）。
 *
 * 语音开关这类配置会被输入框、回答卡片等多处读取，
 * 没必要每个组件各发一次请求；配置页保存后调用 refreshAppConfig 广播更新，
 * 让已打开的页面立即生效（与后端"改完即时生效"的行为一致）。
 */

let cache: api.AppConfigData | null = null;
let inflight: Promise<api.AppConfigData> | null = null;
const listeners = new Set<(c: api.AppConfigData) => void>();

export function useAppConfig(): api.AppConfigData | null {
  const [cfg, setCfg] = useState<api.AppConfigData | null>(cache);

  useEffect(() => {
    if (cache || inflight) return;
    inflight = api
      .fetchAppConfig()
      .then((c) => {
        cache = c;
        listeners.forEach((l) => l(c));
        return c;
      })
      .catch(() => {
        // 配置读取失败时保持 null：各功能按"关闭"处理，不阻断主流程
        return null as unknown as api.AppConfigData;
      })
      .finally(() => {
        inflight = null;
      });
  }, []);

  useEffect(() => {
    const l = (c: api.AppConfigData) => setCfg(c);
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);

  return cfg;
}

/** 配置变更后调用：刷新缓存并通知所有使用方 */
export async function refreshAppConfig(): Promise<void> {
  try {
    const c = await api.fetchAppConfig();
    cache = c;
    listeners.forEach((l) => l(c));
  } catch {
    /* 忽略：下次进入页面会重新拉取 */
  }
}
