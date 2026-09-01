import { Typography } from 'antd';
import { QuestionCircleOutlined } from '@ant-design/icons';
import styles from './ClarifyCard.module.css';

const { Text } = Typography;

/** 澄清反问卡片：问题存在歧义时主动请用户确认，绝不臆测 */
export default function ClarifyCard({
  options,
  reason,
  onPick,
}: {
  options: string[];
  reason?: string;
  onPick: (option: string) => void;
}) {
  return (
    <div className={styles.card}>
      <div className={styles.head}>
        <QuestionCircleOutlined className={styles.icon} />
        <span>需要你补充一点信息</span>
      </div>
      {reason ? <Text type="secondary" className={styles.reason}>{reason}</Text> : null}
      <div className={styles.options}>
        {options.map((o) => (
          <button key={o} className={styles.option} onClick={() => onPick(o)}>
            {o}
          </button>
        ))}
      </div>
    </div>
  );
}
