import { useState } from 'react';
import { CheckCircleFilled, CloseCircleFilled, LoadingOutlined } from '@ant-design/icons';
import type { ThoughtStep } from '@/stores/chatStore';
import styles from './ThoughtSteps.module.css';

/** 5 步可解释链路（对齐 demo 的 renderAaSteps，数据全部来自后端真实执行） */
export default function ThoughtSteps({ steps }: { steps: ThoughtStep[] }) {
  const [open, setOpen] = useState(true);
  if (!steps.length) return null;

  const done = steps.filter((s) => s.status === 'done').length;
  const failed = steps.some((s) => s.status === 'fail');
  const running = steps.some((s) => s.status === 'running');

  return (
    <div className={styles.wrap}>
      <div className={styles.header} onClick={() => setOpen(!open)}>
        <span className={styles.arrow}>{open ? '▾' : '▸'}</span>
        {failed ? (
          <CloseCircleFilled className={styles.fail} />
        ) : running ? (
          <LoadingOutlined className={styles.running} />
        ) : (
          <CheckCircleFilled className={styles.ok} />
        )}
        <span className={styles.summary}>
          {failed ? '执行中断' : running ? '正在分析…' : '已完成'}（{done}/5）
        </span>
        <span className={styles.toggle}>{open ? '点击收起' : '点击展开'}</span>
      </div>

      {open && (
        <div className={styles.body}>
          {steps.map((s) => (
            <div key={s.index} className={`${styles.step} ${styles[s.status]}`}>
              <div className={styles.icon}>
                {s.status === 'done' ? (
                  <CheckCircleFilled />
                ) : s.status === 'fail' ? (
                  <CloseCircleFilled />
                ) : s.status === 'running' ? (
                  <LoadingOutlined />
                ) : (
                  <span className={styles.num}>{s.index}</span>
                )}
              </div>
              <div className={styles.content}>
                <div className={styles.title}>
                  <span className={styles.badge}>{s.index}</span>
                  {s.title}
                  {s.cost_ms ? <span className={styles.cost}>{s.cost_ms}ms</span> : null}
                </div>
                {s.desc ? <pre className={styles.desc}>{s.desc}</pre> : null}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
