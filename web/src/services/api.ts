import { http, unwrap, type PagedData } from './http';

// ── 认证 ─────────────────────────────────────────────────────────
export interface LoginPayload {
  username: string;
  password: string;
  captcha?: string;
  captcha_id?: string;
}
export interface MenuNode {
  id: number;
  parent_id: number;
  name: string;
  path: string;
  component: string;
  icon: string;
  sort_order: number;
  type: string;
  perm_code: string;
  visible: boolean;
}
export interface LoginUser {
  id: number;
  username: string;
  nickname: string;
  phone: string;
  email: string;
  avatar: string;
  status: number;
  pwd_must_change: boolean;
  role_ids: number[];
  role_codes: string[];
  perms: string[];
  menus: MenuNode[];
  data_perms: Record<string, string[] | null>;
}
export interface LoginResult {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: LoginUser;
}
export interface CaptchaResult {
  captcha_id: string;
  image: string;
  required: boolean;
}

export const login = (payload: LoginPayload) =>
  unwrap<LoginResult>(http.post('/auth/login', payload));
export const logout = () => unwrap(http.post('/auth/logout'));
export const fetchProfile = () => unwrap<LoginUser>(http.get('/auth/me'));
export const fetchCaptcha = (username: string) =>
  unwrap<CaptchaResult>(http.get('/auth/captcha', { params: { username } }));
export const changePassword = (oldPassword: string, newPassword: string) =>
  unwrap(http.post('/auth/change-password', { old_password: oldPassword, new_password: newPassword }));

// ── 用户 ─────────────────────────────────────────────────────────
export interface RoleBrief {
  id: number;
  code: string;
  name: string;
}
export interface UserItem {
  id: number;
  username: string;
  nickname: string;
  phone: string;
  email: string;
  status: number;
  valid_until: string;
  last_login_at: string;
  last_login_ip: string;
  pwd_must_change: boolean;
  created_at: string;
  roles: RoleBrief[];
  role_name: string;
}
export interface UserQuery {
  keyword?: string;
  role_id?: number;
  status?: number;
  page?: number;
  page_size?: number;
}
export interface UserPayload {
  username: string;
  password?: string;
  nickname?: string;
  phone?: string;
  email?: string;
  valid_until?: string;
  role_ids: number[];
}

export const fetchUsers = (params: UserQuery) =>
  unwrap<PagedData<UserItem>>(http.get('/users', { params }));
export const createUser = (payload: UserPayload) => unwrap(http.post('/users', payload));
export const updateUser = (id: number, payload: Partial<UserPayload>) =>
  unwrap(http.put(`/users/${id}`, payload));
export const deleteUser = (id: number) => unwrap(http.delete(`/users/${id}`));
export const toggleUserStatus = (id: number) => unwrap(http.put(`/users/${id}/status`));
export const batchResetPassword = (userIds: number[], password = '123456') =>
  unwrap(http.post('/users/batch-reset-password', { user_ids: userIds, password }));

// ── 角色 ─────────────────────────────────────────────────────────
export interface RoleItem {
  id: number;
  code: string;
  name: string;
  description: string;
  is_builtin: boolean;
  user_count: number;
  created_at: string;
  updated_at: string;
}
export interface RolePayload {
  name: string;
  code: string;
  description?: string;
}

export const fetchRoles = (keyword = '', page = 1, pageSize = 20) =>
  unwrap<PagedData<RoleItem>>(http.get('/roles', { params: { keyword, page, page_size: pageSize } }));
export const createRole = (payload: RolePayload) => unwrap(http.post('/roles', payload));
export const updateRole = (id: number, payload: Partial<RolePayload>) =>
  unwrap(http.put(`/roles/${id}`, payload));
export const deleteRole = (id: number) => unwrap(http.delete(`/roles/${id}`));

// ── 菜单 ─────────────────────────────────────────────────────────
export interface MenuTreeNode extends MenuNode {
  ops: string[];
  checked: boolean;
  children: MenuTreeNode[];
}
export interface MenuPayload {
  parent_id: number;
  name: string;
  path?: string;
  component?: string;
  icon?: string;
  sort_order?: number;
  type?: string;
  perm_code?: string;
  visible?: boolean;
}

export const fetchMenuTree = (roleId?: number) =>
  unwrap<MenuTreeNode[]>(http.get('/menus', { params: roleId ? { role_id: roleId } : {} }));
export const createMenu = (payload: MenuPayload) => unwrap(http.post('/menus', payload));
export const updateMenu = (id: number, payload: MenuPayload) =>
  unwrap(http.put(`/menus/${id}`, payload));
export const deleteMenu = (id: number) => unwrap(http.delete(`/menus/${id}`));

// ── 权限配置 ─────────────────────────────────────────────────────
export interface DataPermItem {
  menu_id: number;
  unit_codes: string[];
}
export interface RolePermission {
  role_id: number;
  menus: MenuTreeNode[];
  data_perms: DataPermItem[];
}
export interface SavePermPayload {
  menus: { id: number; checked: boolean; ops: string[] }[];
  data_perms: DataPermItem[];
}
export interface UnitOption {
  code: string;
  name: string;
  region: string;
}

export const fetchRolePermissions = (roleId: number) =>
  unwrap<RolePermission>(http.get(`/roles/${roleId}/permissions`));
export const saveRolePermissions = (roleId: number, payload: SavePermPayload) =>
  unwrap(http.put(`/roles/${roleId}/permissions`, payload));
export const fetchUnits = () => unwrap<UnitOption[]>(http.get('/permissions/units'));

/** 操作权限位（与后端 OPERATIONS 常量一致） */
export const OPERATIONS: [string, string][] = [
  ['view', '查看'],
  ['add', '新增'],
  ['edit', '编辑'],
  ['del', '删除'],
  ['import', '导入'],
  ['export', '导出'],
  ['refresh', '刷新'],
  ['batch', '批量操作'],
  ['filter', '筛选'],
  ['query', '查询'],
];

// ── 操作日志 ─────────────────────────────────────────────────────
export interface OperLogItem {
  id: number;
  user_id: number;
  username: string;
  log_type: string;
  action: string;
  method: string;
  ip: string;
  status: string;
  cost_ms: number;
  created_at: string;
}

export const fetchOperLogs = (params: {
  keyword?: string;
  username?: string;
  log_type?: string;
  status?: string;
  page?: number;
  page_size?: number;
}) => unwrap<PagedData<OperLogItem>>(http.get('/logs/operation', { params }));

// ── 问数会话 ─────────────────────────────────────────────────────
export interface ChatSessionItem {
  id: number;
  title: string;
  pinned: boolean;
  msg_count: number;
  last_msg_at: string;
  user_feedback?: string;
  admin_feedback?: string;
  username?: string;
}
export interface ChatMessageItem {
  id: number;
  session_id: number;
  role: string;
  content: string;
  payload: any;
  created_at: string;
}
export interface SessionDetail extends ChatSessionItem {
  messages: ChatMessageItem[];
  total: number;
}

export const fetchSessions = (keyword = '', page = 1, pageSize = 50) =>
  unwrap<PagedData<ChatSessionItem>>(
    http.get('/chat/sessions', { params: { keyword, page, page_size: pageSize } })
  );
export const createSession = (title = '新对话') =>
  unwrap<{ id: number; title: string }>(http.post('/chat/sessions', { title }));
export const fetchSessionDetail = (id: number, pageSize = 100) =>
  unwrap<SessionDetail>(http.get(`/chat/sessions/${id}`, { params: { page_size: pageSize } }));
export const renameSession = (id: number, title: string) =>
  unwrap(http.put(`/chat/sessions/${id}`, { title }));
export const pinSession = (id: number, pinned: boolean) =>
  unwrap(http.put(`/chat/sessions/${id}/pin`, { pinned }));
export const deleteSession = (id: number) => unwrap(http.delete(`/chat/sessions/${id}`));

export const submitDataError = (messageId: number, comment = '') =>
  unwrap<{ feedback_id: number }>(
    http.post('/chat/data-error', { message_id: messageId, comment })
  );

export const fetchChatLogs = (params: {
  days?: number; username?: string; keyword?: string; page?: number; page_size?: number;
}) => unwrap<PagedData<ChatSessionItem>>(http.get('/chat/logs', { params }));

// ── 元信息（I0）─────────────────────────────────────────────────
export interface OverviewData {
  fact_contract: number;
  fact_ppl: number;
  fact_goal: number;
  dim_unit: number;
  sem_data_source: number;
  sem_metric: number;
  sem_dimension: number;
  sem_rule: number;
  sem_fewshot: number;
}
export const fetchOverview = () => unwrap<OverviewData>(http.get('/meta/overview'));
export const fetchHealth = () => unwrap<Record<string, unknown>>(http.get('/health'));

// ── I4 应用配置 ─────────────────────────────────────────────
export interface AppConfigData {
  greeting: boolean;
  greetingText: string;
  suggestions: boolean;
  tts: boolean;
  stt: boolean;
  modelConfig: boolean;
  hotRecommend: boolean;
  hotThreshold: number;
}
export interface ConfigField {
  key: keyof AppConfigData;
  label: string;
  kind: 'switch' | 'textarea' | 'number';
}
export interface ConfigCard {
  key: string;
  label: string;
  desc: string;
  fields: ConfigField[];
}

export const fetchAppConfig = () => unwrap<AppConfigData>(http.get('/app-config'));
export const fetchAppConfigSchema = () => unwrap<ConfigCard[]>(http.get('/app-config/schema'));
export const saveAppConfig = (configs: Partial<AppConfigData>) =>
  unwrap<{ saved: string[] }>(http.put('/app-config', { configs }));
export const resetAppConfig = () => unwrap<{ reset: boolean }>(http.post('/app-config/reset'));

// ── I4 模型配置 ─────────────────────────────────────────────
export interface ModelItem {
  id: number;
  name: string;
  provider: string;
  base_url: string;
  model_name: string;
  scene: string;
  is_default: boolean;
  enabled: boolean;
  params: Record<string, unknown>;
  api_key_masked: string;
  has_key: boolean;
}
export interface ModelPayload {
  name: string;
  provider?: string;
  base_url: string;
  model_name: string;
  /** 留空表示不修改已保存的密钥 */
  api_key?: string;
  scene?: string;
  is_default?: boolean;
  enabled?: boolean;
}
export interface TestConnResult {
  ok: boolean;
  cost_ms: number;
  message: string;
  reply?: string;
  model?: string;
}

export const fetchModels = (params: { scene?: string; only_enabled?: boolean } = {}) =>
  unwrap<ModelItem[]>(http.get('/models', { params }));
export const createModel = (payload: ModelPayload) =>
  unwrap<{ id: number }>(http.post('/models', payload));
export const updateModel = (id: number, payload: Partial<ModelPayload>) =>
  unwrap<{ id: number }>(http.put(`/models/${id}`, payload));
export const deleteModel = (id: number) => unwrap(http.delete(`/models/${id}`));
export const setDefaultModel = (id: number) =>
  unwrap(http.post(`/models/${id}/default`));
export const testModelConnection = (payload: {
  base_url: string; model_name: string; api_key?: string; provider?: string;
}) => unwrap<TestConnResult>(http.post('/models/test', payload));
export const testSavedModel = (id: number) =>
  unwrap<TestConnResult>(http.post(`/models/${id}/test`));

// ── I4 反馈闭环 ─────────────────────────────────────────────
export interface FeedbackItem {
  id: number;
  question: string;
  user_id: number;
  username: string;
  ai_reply: string;
  session_id: number | null;
  message_id: number | null;
  status: string;
  remark: string;
  handled_by: number | null;
  handled_at: string;
  created_at: string;
}
export interface FeedbackStats {
  total: number;
  todo: number;
  done: number;
  by_status: Record<string, number>;
}
export interface FeedbackQuery {
  status?: string;
  keyword?: string;
  page?: number;
  page_size?: number;
}

export const fetchFeedbacks = (params: FeedbackQuery) =>
  unwrap<PagedData<FeedbackItem>>(http.get('/feedback', { params }));
export const fetchFeedbackStats = () => unwrap<FeedbackStats>(http.get('/feedback/stats'));
export const fetchFeedbackUsers = () => unwrap<string[]>(http.get('/feedback/users/options'));
export const fetchFeedbackDetail = (id: number) =>
  unwrap<FeedbackItem>(http.get(`/feedback/${id}`));
export const handleFeedback = (id: number, status: string, remark: string) =>
  unwrap(http.put(`/feedback/${id}`, { status, remark }));
export const rateMessage = (messageId: number, rating: 'up' | 'down', comment = '') =>
  unwrap(http.post('/feedback/rating', { message_id: messageId, rating, comment }));

// ── I4 快捷提问 ─────────────────────────────────────────────
export interface QuickQuestionItem {
  id: number;
  question: string;
  category: 'recent' | 'recommend' | 'favorite';
  hit_count: number;
  updated_at: string;
}
export interface QuickQuestionTabs {
  recent: QuickQuestionItem[];
  recommend: QuickQuestionItem[];
  favorite: QuickQuestionItem[];
}

export const fetchQuickQuestions = (category?: string) =>
  unwrap<QuickQuestionTabs | QuickQuestionItem[]>(
    http.get('/quick-questions', { params: category ? { category } : {} })
  );
export const addQuickQuestion = (question: string, category = 'favorite') =>
  unwrap<{ id: number }>(http.post('/quick-questions', { question, category }));
export const removeQuickQuestion = (id: number) =>
  unwrap<{ removed: boolean }>(http.delete(`/quick-questions/${id}`));

// ── I4 操作日志导出 ─────────────────────────────────────────
/** 服务端返回 CSV 文件流，这里直接触发浏览器下载 */
export async function downloadOperLogs(params: Record<string, unknown> = {}) {
  const resp = await http.get('/logs/operation/export', {
    params,
    responseType: 'blob',
    timeout: 120000,
  });
  const url = URL.createObjectURL(resp.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = (resp.headers['content-disposition'] || '').match(/filename="(.+?)"/)?.[1]
    || `operation-log-${Date.now()}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ── I5 数据台账 ─────────────────────────────────────────────
export interface LedgerBrief {
  key: string;
  title: string;
  perm: string;
}

export type LedgerDataType = 'text' | 'number' | 'date' | 'bool' | 'enum';

export interface LedgerColumn {
  column: string;
  cn_name: string;
  data_type: LedgerDataType;
  caliber: string;
  visible: boolean;
  filterable: boolean;
  sortable: boolean;
  /** 枚举列与编码列的候选值，供筛选器下拉；高基数列为空（避免下拉几万项） */
  values: unknown[];
}

export type LedgerFilterOp = 'eq' | 'ne' | 'in' | 'contains' | 'gte' | 'lte' | 'between';

export interface LedgerFilter {
  column: string;
  op: LedgerFilterOp;
  value?: unknown;
}

export interface LedgerQueryPayload {
  filters?: LedgerFilter[];
  sort_by?: string;
  sort_dir?: 'asc' | 'desc';
  page?: number;
  page_size?: number;
  columns?: string[];
}

export interface LedgerQueryResult {
  columns: string[];
  rows: unknown[][];
  total: number;
  page: number;
  page_size: number;
  /** 后端实际执行的 SQL（含数据权限注入），便于核对口径 */
  sql: string;
}

export const fetchLedgers = () => unwrap<LedgerBrief[]>(http.get('/ledger/tables'));

export const fetchLedgerColumns = (key: string, withValues = false) =>
  unwrap<LedgerColumn[]>(
    http.get(`/ledger/${key}/columns`, { params: { with_values: withValues } })
  );

export const queryLedger = (key: string, payload: LedgerQueryPayload) =>
  unwrap<LedgerQueryResult>(http.post(`/ledger/${key}/query`, payload));

/** 导入台账 Excel：整批在一个事务里，任一行校验失败都会回滚 */
export const importLedger = (
  key: string,
  file: File,
  mode: 'append' | 'replace' = 'append',
) => {
  const form = new FormData();
  form.append('file', file);
  return unwrap<{ imported: number; mode: string; filename: string }>(
    http.post(`/ledger/${key}/import`, form, {
      params: { mode },
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 180000,
    }),
  );
};

/**
 * 解析 Content-Disposition 文件名：优先 RFC 5987 的 filename*（支持中文），
 * 退回 filename="..."（操作日志导出用的是后者）。
 */
function filenameFrom(header = '', fallback: string): string {
  const star = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (star) {
    try {
      return decodeURIComponent(star[1]);
    } catch {
      /* 编码异常时退回普通文件名 */
    }
  }
  return /filename="([^"]+)"/.exec(header)?.[1] || fallback;
}

/**
 * 导出台账 CSV：与页面同筛选条件，因此不会导出页面上看不到的数据。
 * 返回实际行数与是否被上限截断，供界面提示（避免用户误以为是全量）。
 */
export async function exportLedger(
  key: string,
  payload: LedgerQueryPayload = {},
  fmt: 'csv' | 'xlsx' = 'csv',
): Promise<{ rows: number; truncated: boolean }> {
  const resp = await http.post(`/ledger/${key}/export`, payload, {
    params: { fmt },
    responseType: 'blob',
    timeout: 120000,
  });
  const url = URL.createObjectURL(resp.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filenameFrom(resp.headers['content-disposition'], `${key}.${fmt}`);
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return {
    rows: Number(resp.headers['x-export-rows'] ?? 0),
    truncated: resp.headers['x-export-truncated'] === '1',
  };
}

// ── I5 语义层管理 ───────────────────────────────────────────
// 语义层直接决定问数准确率，改动会立即影响后续所有提问，
// 因此写接口一律在后端校验权限（sem:*:edit）并落操作日志。

export interface SemMetricItem {
  id: number;
  code: string;
  name: string;
  aliases: string[];
  expr_sql: string;
  unit: string;
  caliber: string;
  source_id: number;
  enabled: boolean;
}

export interface SemMetricPayload {
  code: string;
  name: string;
  expr_sql: string;
  source_id: number;
  aliases?: string[];
  unit?: string;
  value_type?: string;
  agg_default?: string;
  caliber?: string;
  default_format?: string;
  enabled?: boolean;
}

export interface SemDimensionItem {
  id: number;
  code: string;
  name: string;
  aliases: string[];
  expr_sql: string;
  join_sql: string;
  dim_type: string;
  source_id: number;
  enabled: boolean;
}

export interface SemDimensionPayload {
  code: string;
  name: string;
  expr_sql: string;
  source_id: number;
  aliases?: string[];
  display_expr?: string;
  join_sql?: string;
  dim_type?: string;
  value_map?: Record<string, unknown> | null;
  enabled?: boolean;
}

export interface SemRuleItem {
  id: number;
  scene: string;
  title: string;
  content: string;
  priority: number;
  enabled: boolean;
}

export interface SemRulePayload {
  scene: string;
  title: string;
  content: string;
  priority?: number;
  enabled?: boolean;
}

export interface SemFewshotItem {
  id: number;
  question: string;
  rewritten: string;
  sql: string;
  notes: string;
  hit_count: number;
  verified: boolean;
}

export interface SemFewshotPayload {
  question: string;
  sql: string;
  rewritten?: string;
  notes?: string;
  verified?: boolean;
}

export const fetchSemMetrics = () =>
  unwrap<SemMetricItem[]>(http.get('/data-sources/metrics'));
export const createSemMetric = (p: SemMetricPayload) =>
  unwrap<{ id: number }>(http.post('/semantic/metrics', p));
export const updateSemMetric = (id: number, p: SemMetricPayload) =>
  unwrap<{ id: number }>(http.put(`/semantic/metrics/${id}`, p));
export const deleteSemMetric = (id: number) =>
  unwrap<{ id: number }>(http.delete(`/semantic/metrics/${id}`));

export const fetchSemDimensions = () =>
  unwrap<SemDimensionItem[]>(http.get('/data-sources/dimensions'));
export const createSemDimension = (p: SemDimensionPayload) =>
  unwrap<{ id: number }>(http.post('/semantic/dimensions', p));
export const updateSemDimension = (id: number, p: SemDimensionPayload) =>
  unwrap<{ id: number }>(http.put(`/semantic/dimensions/${id}`, p));
export const deleteSemDimension = (id: number) =>
  unwrap<{ id: number }>(http.delete(`/semantic/dimensions/${id}`));

export const fetchSemRules = () => unwrap<SemRuleItem[]>(http.get('/semantic/rules'));
export const createSemRule = (p: SemRulePayload) =>
  unwrap<{ id: number }>(http.post('/semantic/rules', p));
export const updateSemRule = (id: number, p: SemRulePayload) =>
  unwrap<{ id: number }>(http.put(`/semantic/rules/${id}`, p));
export const deleteSemRule = (id: number) =>
  unwrap<{ id: number }>(http.delete(`/semantic/rules/${id}`));

export const fetchSemFewshots = (params: { keyword?: string; page_size?: number } = {}) =>
  unwrap<{ total: number; items: SemFewshotItem[] }>(
    http.get('/semantic/fewshots', { params: { page_size: 200, ...params } }),
  );
export const createSemFewshot = (p: SemFewshotPayload) =>
  unwrap<{ id: number }>(http.post('/semantic/fewshots', p));
export const updateSemFewshot = (id: number, p: SemFewshotPayload) =>
  unwrap<{ id: number }>(http.put(`/semantic/fewshots/${id}`, p));
export const deleteSemFewshot = (id: number) =>
  unwrap<{ id: number }>(http.delete(`/semantic/fewshots/${id}`));

/**
 * 真跑一遍样本 SQL：表结构变更后历史样本可能失效，
 * 失效样本混在 Prompt 里会误导模型，所以提供单条即时验证。
 */
export const verifySemFewshot = (id: number) =>
  unwrap<{ ok: boolean; rows: number; error?: string }>(
    http.post(`/semantic/fewshots/${id}/verify`),
  );

// ── I5 运行看板 ───────────────────────────────────────────────
export interface QaStats {
  days: number;
  total: number;
  ok: number;
  failed: number;
  success_rate: number;
  p50_ms: number;
  p95_ms: number;
  avg_ms: number;
  cached: number;
  cache_hit_rate: number;
  trend: { date: string; total: number; ok: number; avg_ms: number }[];
}

/** 问数运行看板：成功率 / 耗时分位 / 缓存命中（普通用户只看自己的） */
export const fetchQaStats = (days = 7) =>
  unwrap<QaStats>(http.get('/stats/qa', { params: { days } }));
