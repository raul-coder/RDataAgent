import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  App, Button, Card, Checkbox, DatePicker, Dropdown, Input, InputNumber, Modal,
  Popover, Select, Space, Switch, Table, Tooltip, Typography, Upload,
} from 'antd';
import {
  DeleteOutlined, DownloadOutlined, ImportOutlined, PlusOutlined, ReloadOutlined,
  SettingOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import dayjs, { type Dayjs } from 'dayjs';
import * as api from '@/services/api';
import styles from './LedgerTable.module.css';

const { Text, Paragraph } = Typography;

interface Props {
  ledgerKey: string;
  title: string;
}

interface FilterRow {
  id: number;
  column: string;
  value: unknown;
}

/**
 * 文本筛选：输入防抖后再查询。
 * 否则每敲一个字符都会发一次请求（输入"扩容"就是 2 次全表查询）。
 */
function TextFilterInput({
  value, onChange, placeholder,
}: {
  value: unknown;
  onChange: (v: unknown) => void;
  placeholder: string;
}) {
  const [text, setText] = useState(String(value ?? ''));

  // 外部清空筛选 / 切换列时，把输入框同步回外部状态
  useEffect(() => {
    setText(String(value ?? ''));
  }, [value]);

  useEffect(() => {
    if (text === String(value ?? '')) return;
    const timer = setTimeout(() => onChange(text), 400);
    return () => clearTimeout(timer);
  }, [text, value, onChange]);

  return (
    <Input
      allowClear
      style={{ width: 200 }}
      placeholder={placeholder}
      value={text}
      onChange={(e) => setText(e.target.value)}
    />
  );
}

/** 单个筛选项的输入控件：按列的数据类型自适应 */
function FilterInput({
  col, value, onChange,
}: {
  col: api.LedgerColumn;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  if (col.data_type === 'enum' || (col.values?.length ?? 0) > 0) {
    return (
      <Select
        mode="multiple"
        allowClear
        style={{ minWidth: 220 }}
        placeholder="选择或输入筛选"
        value={(value as unknown[]) ?? []}
        onChange={onChange}
        options={(col.values ?? []).map((v) => ({ label: String(v), value: v }))}
        maxTagCount={3}
      />
    );
  }
  if (col.data_type === 'number') {
    const [lo, hi] = (value as [number | null, number | null]) ?? [null, null];
    return (
      <Space>
        <InputNumber
          placeholder="最小" value={lo ?? null}
          onChange={(v) => onChange([v, hi])}
        />
        <span>~</span>
        <InputNumber
          placeholder="最大" value={hi ?? null}
          onChange={(v) => onChange([lo, v])}
        />
      </Space>
    );
  }
  if (col.data_type === 'date') {
    const pair = (value as [string | null, string | null]) ?? [null, null];
    const rangeValue: [Dayjs | null, Dayjs | null] | null =
      pair[0] || pair[1]
        ? [pair[0] ? dayjs(pair[0]) : null, pair[1] ? dayjs(pair[1]) : null]
        : null;
    return (
      <DatePicker.RangePicker
        value={rangeValue}
        onChange={(d) =>
          onChange([
            d?.[0] ? d[0].format('YYYY-MM-DD') : null,
            d?.[1] ? d[1].format('YYYY-MM-DD') : null,
          ])
        }
      />
    );
  }
  if (col.data_type === 'bool') {
    return (
      <Select
        allowClear
        style={{ width: 120 }}
        placeholder="是 / 否"
        value={value as boolean | undefined}
        onChange={onChange}
        options={[{ label: '是', value: true }, { label: '否', value: false }]}
      />
    );
  }
  return (
    <TextFilterInput value={value} onChange={onChange} placeholder="包含的文本" />
  );
}

/** 把界面上的筛选行转成本接口要求的 filters */
function toFilters(rows: FilterRow[], cols: api.LedgerColumn[]): api.LedgerFilter[] {
  const out: api.LedgerFilter[] = [];
  for (const r of rows) {
    const col = cols.find((c) => c.column === r.column);
    if (!col) continue;
    const v = r.value;
    if (v === undefined || v === null || v === '' ) continue;

    if (col.data_type === 'number') {
      const [lo, hi] = (v as [number | null, number | null]) ?? [null, null];
      if (lo == null && hi == null) continue;
      out.push({ column: r.column, op: 'between', value: [lo, hi] });
    } else if (col.data_type === 'date') {
      const [lo, hi] = (v as [string | null, string | null]) ?? [null, null];
      if (!lo && !hi) continue;
      out.push({ column: r.column, op: 'between', value: [lo, hi] });
    } else if (col.data_type === 'bool') {
      out.push({ column: r.column, op: 'eq', value: v });
    } else if ((col.values?.length ?? 0) > 0 || col.data_type === 'enum') {
      if (Array.isArray(v) && v.length === 0) continue;
      out.push({ column: r.column, op: 'in', value: v });
    } else {
      out.push({ column: r.column, op: 'contains', value: v });
    }
  }
  return out;
}

/**
 * 台账查看（FR-D1）：列筛选 + 排序 + 分页 + 导出。
 * 三张台账共用本组件，仅 key 与标题不同。
 */
export default function LedgerTable({ ledgerKey, title }: Props) {
  const { message, modal } = App.useApp();
  const [cols, setCols] = useState<api.LedgerColumn[]>([]);
  const [rows, setRows] = useState<FilterRow[]>([]);
  const [sortBy, setSortBy] = useState<string | undefined>();
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [data, setData] = useState<api.LedgerQueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [showAll, setShowAll] = useState(false);
  const [importing, setImporting] = useState(false);

  // 列定义只需加载一次（切换台账时 key 变化会重新加载）
  useEffect(() => {
    setCols([]);
    setRows([]);
    setSortBy(undefined);
    setPage(1);
    setHidden(new Set());
    setShowAll(false);
    api.fetchLedgerColumns(ledgerKey, true)
      .then(setCols)
      .catch((e: Error) => message.error(e.message));
  }, [ledgerKey, message]);

  const activeCols = useMemo(
    () => cols.filter((c) => (showAll || c.visible) && !hidden.has(c.column)),
    [cols, hidden, showAll],
  );

  const load = useCallback(async () => {
    if (!cols.length) return;
    setLoading(true);
    try {
      const res = await api.queryLedger(ledgerKey, {
        filters: toFilters(rows, cols),
        sort_by: sortBy,
        sort_dir: sortDir,
        page,
        page_size: pageSize,
        columns: activeCols.map((c) => c.column),
      });
      setData(res);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [ledgerKey, cols, rows, sortBy, sortDir, page, pageSize, activeCols, message]);

  useEffect(() => {
    void load();
    // load 依赖会随筛选变化自动重查，无需额外触发
  }, [load]);

  const columns: ColumnsType<unknown[]> = useMemo(
    () =>
      activeCols.map((c, i) => ({
        title: c.cn_name,
        dataIndex: i,
        key: c.column,
        width: c.data_type === 'text' ? 160 : 110,
        ellipsis: c.data_type === 'text',
        align: c.data_type === 'number' ? 'right' : 'left',
        sorter: c.sortable,
        sortOrder: sortBy === c.column ? (sortDir === 'asc' ? 'ascend' : 'descend') : null,
        render: (v: unknown) => {
          if (v === null || v === undefined) return <Text type="secondary">—</Text>;
          if (c.data_type === 'bool') return v ? '是' : '否';
          if (c.data_type === 'number' && typeof v === 'number') {
            return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, {
              minimumFractionDigits: 2, maximumFractionDigits: 2,
            });
          }
          return String(v);
        },
      })),
    [activeCols, sortBy, sortDir],
  );

  const addFilter = () => {
    const first = cols.find((c) => c.filterable && !rows.some((r) => r.column === c.column));
    if (!first) {
      message.info('所有可筛选列都已添加');
      return;
    }
    setRows([...rows, { id: Date.now(), column: first.column, value: undefined }]);
  };

  const unusedCols = cols.filter(
    (c) => c.filterable && !rows.some((r) => r.column === c.column),
  );

  const exportCsv = async (fmt: 'csv' | 'xlsx' = 'csv') => {
    try {
      const { rows: n, truncated } = await api.exportLedger(ledgerKey, {
        filters: toFilters(rows, cols),
        sort_by: sortBy,
        sort_dir: sortDir,
        columns: activeCols.map((c) => c.column),
      }, fmt);
      if (truncated) {
        message.warning(
          `已导出 ${n.toLocaleString()} 行（达到单次上限，请缩小筛选范围后分批导出）`,
        );
      } else {
        message.success(`已导出 ${n.toLocaleString()} 行`);
      }
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  /** 导入：默认追加；「清空后导入」需用户主动勾选，避免误删整张台账 */
  const doImport = async (file: File, mode: 'append' | 'replace') => {
    setImporting(true);
    try {
      const r = await api.importLedger(ledgerKey, file, mode);
      message.success(
        `已导入 ${r.imported} 行（${mode === 'append' ? '追加' : '替换'}）`,
      );
      void load();
    } catch (e) {
      // 校验错误信息较长（含行号与列名），弹窗比一行 message 可读
      modal.error({
        title: '导入失败，已回滚',
        width: 640,
        content: (
          <div style={{ whiteSpace: 'pre-wrap', fontSize: 13 }}>
            {(e as Error).message}
          </div>
        ),
      });
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className={styles.wrap}>
      <Card
        size="small"
        title={title}
        extra={(
          <Space>
            <Tooltip title="显示所有列（含分月/分季明细）">
              <Space size={4}>
                <Switch size="small" checked={showAll} onChange={setShowAll} />
                <Text type="secondary" style={{ fontSize: 12 }}>全部列</Text>
              </Space>
            </Tooltip>
            <Popover
              trigger="click"
              placement="bottomRight"
              title="列显示"
              content={(
                <div style={{ maxHeight: 320, overflowY: 'auto' }}>
                  {cols.map((c) => (
                    <div key={c.column} style={{ padding: '2px 0' }}>
                      <label>
                        <input
                          type="checkbox"
                          checked={!hidden.has(c.column)}
                          onChange={(e) => {
                            const next = new Set(hidden);
                            if (e.target.checked) next.delete(c.column);
                            else next.add(c.column);
                            setHidden(next);
                          }}
                        />{' '}
                        {c.cn_name}
                      </label>
                    </div>
                  ))}
                </div>
              )}
            >
              <Button size="small" icon={<SettingOutlined />}>列</Button>
            </Popover>
            <Button size="small" icon={<ReloadOutlined />} onClick={() => void load()}>
              刷新
            </Button>
            <Dropdown.Button
              size="small"
              type="primary"
              icon={<DownloadOutlined />}
              onClick={() => void exportCsv('csv')}
              menu={{
                items: [
                  { key: 'csv', label: '导出 CSV' },
                  { key: 'xlsx', label: '导出 Excel' },
                ],
                onClick: ({ key }) => void exportCsv(key as 'csv' | 'xlsx'),
              }}
            >
              导出
            </Dropdown.Button>

            <Upload
              accept=".xlsx,.xls"
              showUploadList={false}
              beforeUpload={(file) => {
                let replace = false;
                modal.confirm({
                  title: '确认导入',
                  width: 540,
                  content: (
                    <Space direction="vertical" size={6}>
                      <Text>文件：{file.name}</Text>
                      <Checkbox onChange={(e) => { replace = e.target.checked; }}>
                        清空现有数据后导入（不可恢复）
                      </Checkbox>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        表头须为中文列名（可先点「导出」拿文件当模板）；
                        编码列可直接填名称，如「上海代表处」。
                        整批在一个事务内，任一行校验失败都会回滚。
                      </Text>
                    </Space>
                  ),
                  okText: '开始导入',
                  cancelText: '取消',
                  onOk: () =>
                    void doImport(
                      file as unknown as File,
                      replace ? 'replace' : 'append',
                    ),
                });
                return false;   // 阻止 antd 自动上传，由 doImport 统一处理
              }}
            >
              <Button size="small" icon={<ImportOutlined />} loading={importing}>
                导入
              </Button>
            </Upload>
          </Space>
        )}
      >
        <Space direction="vertical" style={{ width: '100%' }} size={8}>
          <Space wrap size={8}>
            <Button size="small" icon={<PlusOutlined />} onClick={addFilter}>
              添加筛选
            </Button>
            {rows.map((r) => {
              const col = cols.find((c) => c.column === r.column);
              if (!col) return null;
              return (
                <Space key={r.id} size={4} className={styles.filterRow}>
                  <Select
                    size="small"
                    style={{ width: 130 }}
                    value={r.column}
                    onChange={(v) =>
                      setRows(rows.map((x) =>
                        x.id === r.id ? { ...x, column: v, value: undefined } : x))
                    }
                    options={[...(col ? [col] : []), ...unusedCols].map((c) => ({
                      label: c.cn_name, value: c.column,
                    }))}
                  />
                  <FilterInput
                    col={col}
                    value={r.value}
                    onChange={(v) =>
                      setRows(rows.map((x) => (x.id === r.id ? { ...x, value: v } : x)))}
                  />
                  <Button
                    size="small" type="text" icon={<DeleteOutlined />}
                    onClick={() => setRows(rows.filter((x) => x.id !== r.id))}
                  />
                </Space>
              );
            })}
            {rows.length > 0 && (
              <Button size="small" type="link" onClick={() => setRows([])}>清空</Button>
            )}
          </Space>

          <Table<unknown[]>
            size="small"
            rowKey={(_, i) => String(i)}
            loading={loading}
            columns={columns}
            dataSource={(data?.rows ?? []) as unknown[][]}
            scroll={{ x: 'max-content', y: 480 }}
            onChange={(_p, _f, sorter) => {
              const s = Array.isArray(sorter) ? sorter[0] : sorter;
              if (!s?.order) {
                setSortBy(undefined);
                return;
              }
              setSortBy(String(s.columnKey));
              setSortDir(s.order === 'ascend' ? 'asc' : 'desc');
            }}
            pagination={{
              current: page,
              pageSize,
              total: data?.total ?? 0,
              showSizeChanger: true,
              showTotal: (t) => `共 ${t.toLocaleString()} 行`,
              onChange: (p, ps) => { setPage(p); setPageSize(ps); },
            }}
          />

          {data?.sql && (
            <details className={styles.sqlBox}>
              <summary>查看本次查询的 SQL（含数据权限过滤）</summary>
              <Paragraph code copyable={{ text: data.sql }} className={styles.sql}>
                {data.sql}
              </Paragraph>
            </details>
          )}
        </Space>
      </Card>
    </div>
  );
}
