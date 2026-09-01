import { Button, Tooltip } from 'antd';
import { CopyOutlined, EditOutlined, SendOutlined } from '@ant-design/icons';
import styles from './UserMessage.module.css';

interface Props {
  content: string;
  onEdit?: (text: string) => void;
}

/** 用户消息气泡：悬浮显示「复制 / 编辑重发」（对齐 demo 的 qa-user-actions） */
export default function UserMessage({ content, onEdit }: Props) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(content);
    } catch {
      /* 忽略 */
    }
  };

  return (
    <div className={styles.row}>
      <div className={styles.bubble}>{content}</div>
      <div className={styles.actions}>
        <Tooltip title="复制">
          <Button type="text" size="small" icon={<CopyOutlined />} onClick={copy} />
        </Tooltip>
        {onEdit && (
          <Tooltip title="编辑并重发">
            <Button
              type="text"
              size="small"
              icon={<EditOutlined />}
              onClick={() => {
                const next = window.prompt('编辑问题后重发', content);
                if (next && next.trim() && next !== content) onEdit(next.trim());
              }}
            />
          </Tooltip>
        )}
      </div>
    </div>
  );
}
