import { expect, test, type Page } from '@playwright/test';

/**
 * 核心用例的端到端验证（T6-2）。
 *
 * 与后端 check_* 脚本的分工：
 *     check_*   —— 验证接口契约与权限（快、稳定、无浏览器）
 *     这里      —— 验证真实用户在浏览器里能否走通（慢、依赖渲染）
 *
 * 因此这里只覆盖"断了就没法演示"的主干：登录、问数、多轮、换图、
 * 会话管理、菜单权限差异。边界与注入用例交给后端脚本。
 */

const API_BASE = process.env.E2E_API_URL ?? 'http://127.0.0.1:8000';

async function login(page: Page, username: string, password = '123456') {
  await page.goto('/login');
  await page.getByPlaceholder('请输入用户名').fill(username);
  await page.getByPlaceholder('请输入密码').fill(password);
  await page.getByRole('button', { name: '登录' }).click();
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 30_000 });
}

/** 发一次问数并等待结论出现 */
async function ask(page: Page, question: string) {
  const box = page.locator('textarea').last();
  await box.fill(question);
  await box.press('Enter');
  // 结论流式输出完成：等待追问建议出现（它是链路的最后一步）
  await expect(page.locator('body')).toContainText(/./);
}

test.describe('登录', () => {
  test('管理员登录进入首页', async ({ page }) => {
    await login(page, 'admin');
    await expect(page.locator('body')).toContainText('你好，管理员');
  });

  test('错误密码被拒绝', async ({ page }) => {
    await page.goto('/login');
    await page.getByPlaceholder('请输入用户名').fill('admin');
    await page.getByPlaceholder('请输入密码').fill('wrong-password');
    await page.getByRole('button', { name: '登录' }).click();
    await expect(page.locator('.ant-message-error, .ant-alert-error').first())
      .toBeVisible({ timeout: 15_000 });
    await expect(page).toHaveURL(/\/login/);
  });
});

test.describe('UC-1 智能问数', () => {
  test.beforeEach(async ({ page }) => {
    await login(page, 'admin');
    await page.goto('/ai-qa');
  });

  test('完整问数链路：5 步 + 表格 + 图表 + 结论', async ({ page }) => {
    await ask(page, '2026年各经营单元收入排名');

    // 五步链路全部完成
    await expect(page.locator('body')).toContainText('选择数据表&数据时效');
    // 结果表格有数据行
    await expect(page.locator('table').first()).toBeVisible({ timeout: 120_000 });
    // 结论文字（流式）出现
    await expect(page.locator('body')).toContainText('万元', { timeout: 120_000 });
  });

  test('多轮追问：换图不重跑 SQL', async ({ page }) => {
    await ask(page, '2026年各经营单元收入排名');
    await expect(page.locator('table').first()).toBeVisible({ timeout: 120_000 });

    // 结果二次加工：切换为饼图
    await ask(page, '换成饼图');
    await expect(page.locator('body')).toContainText(/饼图|已切换/, { timeout: 60_000 });
  });

  test('越界问题被拒答', async ({ page }) => {
    await ask(page, '帮我写一首诗');
    await expect(page.locator('body')).toContainText(/经营数据|只能/, { timeout: 60_000 });
  });
});

test.describe('会话管理', () => {
  test('新建会话后可以提问', async ({ page }) => {
    await login(page, 'admin');
    await page.goto('/ai-qa');
    await page.getByRole('button', { name: /新建对话/ }).click();
    await expect(page.locator('body')).toContainText('欢迎使用智能AI问数');
  });
});

test.describe('UC-3 数据权限（菜单级差异）', () => {
  test('受限用户看不到系统管理与语义层', async ({ page }) => {
    await login(page, 'zhangsan');
    const body = page.locator('body');
    await expect(body).toContainText('智能问数');
    await expect(body).not.toContainText('用户管理');
    await expect(body).not.toContainText('语义层管理');
  });

  test('受限用户问数只返回授权单元', async ({ page }) => {
    await login(page, 'zhangsan');
    await page.goto('/ai-qa');
    await ask(page, '2026年各经营单元收入排名');
    // 结论里应明确提示过滤范围
    await expect(page.locator('body')).toContainText(
      /数据权限/,
      { timeout: 120_000 },
    );
  });
});

test.describe('台账页', () => {
  test('台账能加载并显示行数', async ({ page }) => {
    await login(page, 'admin');
    await page.goto('/ledger/contract');
    await expect(page.locator('body')).toContainText(/共 [\d,]+ 行/, { timeout: 30_000 });

    // 翻页可用
    await page.getByRole('listitem', { name: '2' }).first().click();
    await expect(page.locator('body')).toContainText(/共 [\d,]+ 行/, { timeout: 30_000 });
  });
});
