import { useCallback, useEffect, useState } from 'react';
import { App, Button, Empty, List, Popover, Space, Tabs, Tooltip, Typography } from 'antd';
import { DeleteOutlined, QuestionCircleOutlined, StarFilled, StarOutlined } from '@ant-design/icons';
import * as api from '@/services/api';

const { Text } = Typography;

interface Props {
  onPick: (q: string) => void;
  /** 当前输入框内容，可一键收藏 */
  currentQuestion?: string;
}

const TAB_META = [
  { key: 'recommend', label: '推荐' },
  { key: 'recent', label: '常问' },
  { key: 'favorite', label: '收藏' },
];

/**
 * 快捷提问面板（FR-Q23 / Q24）：
 *   推荐 —— 系统预置；常问 —— 按提问频次自动生成（受应用配置阈值控制）；
 *   收藏 —— 用户手动星标。
 */
export default function QuickPanel({ onPick, currentQuestion }: Props) {
  const { message } = App.useApp();
  const [open, setOpen] = useState(false);
  const [tabs, setTabs] = useState<api.QuickQuestionTabs>({
    recent: [], recommend: [], favorite: [],
  });
  const [active, setActive] = useState('recommend');
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.fetchQuickQuestions();
      // 不传 category 时返回三个 Tab，传时返回数组；这里统一按对象处理
      setTabs(data as api.QuickQuestionTabs);
    } catch {
      /* 面板属于增强功能，加载失败静默降级为空列表 */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const addFav = async (q: string) => {
    try {
      await api.addQuickQuestion(q, 'favorite');
      message.success('已收藏');
      void load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const removeItem = async (id: number) => {
    try {
      await api.removeQuickQuestion(id);
      void load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const items = (tabs[active as keyof api.QuickQuestionTabs] ?? []) as api.QuickQuestionItem[];

  const content = (
    <div style={{ width: 380 }}>
      <Tabs
        size="small"
        activeKey={active}
        onChange={setActive}
        items={TAB_META.map((t) => ({
          key: t.key,
          label: `${t.label}(${(tabs[t.key as keyof api.QuickQuestionTabs] as api.QuickQuestionItem[]).length})`,
        }))}
      />
      {items.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            active === 'recent'
              ? '还没有常问问题，多问几次会自动生成'
              : active === 'favorite'
                ? '暂无收藏，点击问题的星标即可收藏'
                : '暂无推荐问题'
          }
          style={{ padding: '12px 0' }}
        />
      ) : (
        <List
          size="small"
          loading={loading}
          dataSource={items}
          style={{ maxHeight: 320, overflowY: 'auto' }}
          renderItem={(it) => (
            <List.Item
              style={{ padding: '6px 4px', cursor: 'pointer' }}
              onClick={() => {
                onPick(it.question);
                setOpen(false);
              }}
              actions={[
                active === 'favorite' ? (
                  <Tooltip title="移除收藏" key="del">
                    <Button
                      type="text"
                      size="small"
                      icon={<DeleteOutlined />}
                      onClick={(e) => {
                        e.stopPropagation();
                        void removeItem(it.id);
                      }}
                    />
                  </Tooltip>
                ) : (
                  <Tooltip title="收藏" key="fav">
                    <Button
                      type="text"
                      size="small"
                      icon={<StarOutlined />}
                      onClick={(e) => {
                        e.stopPropagation();
                        void addFav(it.question);
                      }}
                    />
                  </Tooltip>
                ),
              ]}
            >
              <Space>
                <Text>{it.question}</Text>
                {it.hit_count > 0 && (
                  <Text type="secondary" style={{ fontSize: 11 }}>{it.hit_count} 次</Text>
                )}
              </Space>
            </List.Item>
          )}
        />
      )}

      {currentQuestion?.trim() && (
        <div style={{ borderTop: '1px solid #f0f0f0', paddingTop: 8, marginTop: 4 }}>
          <Button
            type="link"
            size="small"
            icon={<StarFilled />}
            onClick={() => void addFav(currentQuestion.trim())}
          >
            收藏当前输入：「{currentQuestion.trim().slice(0, 16)}
            {currentQuestion.trim().length > 16 ? '…' : ''}」
          </Button>
        </div>
      )}
    </div>
  );

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      trigger="click"
      placement="topLeft"
      content={content}
      title="快捷提问"
    >
      <Tooltip title="快捷提问">
        <Button type="text" icon={<QuestionCircleOutlined />} />
      </Tooltip>
    </Popover>
  );
}
