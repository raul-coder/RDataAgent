import { defineConfig } from '@playwright/test';

/**
 * 端到端测试配置（T6-2）。
 *
 * 超时刻意放宽：问数链路最坏情况要经历「SQL 生成 → 校验失败 → 自愈重试」，
 * 冷缓存下单条可能超过 60s。跑之前建议先 make warmup-cache。
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 180_000,
  expect: { timeout: 60_000 },
  fullyParallel: false,   // 共享同一个后端与数据库，并行会互相干扰
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5181',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    locale: 'zh-CN',
  },
});
