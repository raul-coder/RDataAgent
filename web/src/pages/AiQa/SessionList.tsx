import { useState } from 'react';
import { App, Button, Dropdown, Empty, Input, Modal, Tabs } from 'antd';
import {
  DeleteOutlined, EditOutlined, MessageOutlined, MoreOutlined,
  PlusOutlined, PushpinFilled, PushpinOutlined, SearchOutlined,
} from '@ant-design/icons';
import LogList from './LogList';
import styles from './SessionList.module.css';

export interface SessionBrief {
  id: number;
  title: string;
  pinned: boolean;
  msg_count: number;
  last_msg_at: string;
}

interface Props {
  sessions: SessionBrief[];
  currentId: number | null;
  loading?: boolean;
  onSelect: (id: number) => void;
  onCreate: () => void;
  onRename: (id: number, title: string) => void;
  onDelete: (id: number) => void;
  onPin: (id: number, pinned: boolean) => void;
  onSearch: (keyword: string) => void;
}

/** 左侧栏：会话列表 / 问数日志 双 Tab（对齐 demo 的 switchQaTab） */
export default function SessionList({
  sessions, currentId, onSelect, onCreate, onRename, onDelete, onPin, onSearch,
}: Props) {
  const { modal } = App.useApp();
  const [tab, setTab] = useState('session');

  const confirmDelete = (s: SessionBrief) => {
    modal.confirm({
      title: `删除会话「${s.title}」？`,
      content: '删除后不可恢复。',
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => onDelete(s.id),
    });
  };

  return (
    <div className={styles.wrap}>
      <Tabs
        activeKey={tab}
        onChange={setTab}
        size="small"
        className={styles.tabs}
        items={[
          { key: 'session', label: '会话' },
          { key: 'log', label: '问数日志' },
        ]}
      />

      {tab === 'log' ? (
        <div className={styles.logPane}>
          <LogList />
        </div>
      ) : (
        <>
          <div className={styles.header}>
            <Button type="primary" icon={<PlusOutlined />} block onClick={onCreate}>
              新建对话
            </Button>
          </div>

          <div className={styles.search}>
            <Input
              allowClear
              size="small"
              prefix={<SearchOutlined />}
              placeholder="搜索会话标题"
              onChange={(e) => onSearch(e.target.value)}
            />
          </div>

          <div className={styles.list}>
            {sessions.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无会话" />
            ) : (
              sessions.map((s) => (
                <div
                  key={s.id}
                  className={`${styles.item} ${s.id === currentId ? styles.active : ''} ${s.pinned ? styles.pinned : ''}`}
                  onClick={() => onSelect(s.id)}
                >
                  <MessageOutlined className={styles.icon} />
                  <div className={styles.body}>
                    <div className={styles.title} title={s.title}>{s.title}</div>
                    <div className={styles.meta}>{s.msg_count} 条消息 · {s.last_msg_at.slice(5, 16)}</div>
                  </div>
                  {s.pinned && <PushpinFilled className={styles.pinIcon} />}
                  <Dropdown
                    trigger={['click']}
                    menu={{
                      items: [
                        { key: 'pin', icon: s.pinned ? <PushpinOutlined /> : <PushpinFilled />,
                          label: s.pinned ? '取消置顶' : '置顶' },
                        { key: 'rename', icon: <EditOutlined />, label: '重命名' },
                        { type: 'divider' },
                        { key: 'delete', icon: <DeleteOutlined />, label: '删除', danger: true },
                      ],
                      onClick: ({ key, domEvent }) => {
                        domEvent.stopPropagation();
                        if (key === 'pin') onPin(s.id, !s.pinned);
                        else if (key === 'rename') {
                          let next = s.title;
                          Modal.confirm({
                            title: '重命名会话',
                            content: (
                              <Input
                                defaultValue={s.title}
                                onChange={(e) => { next = e.target.value; }}
                                maxLength={60}
                              />
                            ),
                            okText: '保存',
                            cancelText: '取消',
                            onOk: () => next.trim() && onRename(s.id, next.trim()),
                          });
                        } else if (key === 'delete') confirmDelete(s);
                      },
                    }}
                  >
                    <MoreOutlined
                      className={styles.more}
                      onClick={(e) => e.stopPropagation()}
                    />
                  </Dropdown>
                </div>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}
