import { useCallback, useEffect, useState } from 'react';
import {
  App, Button, Card, Col, Input, InputNumber, Row, Space, Spin, Switch, Tag, Typography,
} from 'antd';
import { ReloadOutlined, UndoOutlined } from '@ant-design/icons';
import * as api from '@/services/api';
import { refreshAppConfig } from '@/hooks/useAppConfig';

const { Text, Paragraph } = Typography;

/**
 * 应用配置：6 张卡片。
 * 卡片结构由后端 /app-config/schema 下发，前端不硬编码字段，
 * 因此新增配置项时两端都不用改渲染逻辑。
 */
export default function AppConfig() {
  const { message, modal } = App.useApp();
  const [cards, setCards] = useState<api.ConfigCard[]>([]);
  const [values, setValues] = useState<api.AppConfigData | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [schema, cfg] = await Promise.all([
        api.fetchAppConfigSchema(),
        api.fetchAppConfig(),
      ]);
      setCards(schema);
      setValues(cfg);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { void load(); }, [load]);

  const setValue = (key: string, value: unknown) =>
    setValues((prev) => (prev ? ({ ...prev, [key]: value } as api.AppConfigData) : prev));

  const saveCard = async (card: api.ConfigCard) => {
    if (!values) return;
    // 字段类型随 key 变化（boolean / string / number），
    // 逐字段赋值无法通过类型窄化，这里统一按字典处理
    const payload: Record<string, unknown> = {};
    card.fields.forEach((f) => {
      payload[f.key] = values[f.key];
    });
    setSaving(card.key);
    try {
      await api.saveAppConfig(payload);
      // 广播给已打开的其他页面：问数页据此显示/隐藏麦克风与朗读按钮
      await refreshAppConfig();
      message.success(`「${card.label}」已保存，立即生效`);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(null);
    }
  };

  const resetAll = () => {
    modal.confirm({
      title: '恢复默认配置',
      content: '将把全部 6 项配置恢复为系统默认值，当前修改会丢失。',
      okText: '确认恢复',
      cancelText: '取消',
      onOk: async () => {
        await api.resetAppConfig();
        await refreshAppConfig();
        message.success('已恢复默认配置');
        void load();
      },
    });
  };

  const renderField = (field: api.ConfigField) => {
    if (!values) return null;
    const raw = values[field.key];
    if (field.kind === 'switch') {
      return (
        <Switch
          checked={Boolean(raw)}
          checkedChildren="开"
          unCheckedChildren="关"
          onChange={(v) => setValue(field.key, v)}
        />
      );
    }
    if (field.kind === 'number') {
      return (
        <InputNumber
          min={1}
          max={100}
          value={Number(raw ?? 0)}
          onChange={(v) => setValue(field.key, v ?? 1)}
          addonAfter="次"
        />
      );
    }
    return (
      <Input.TextArea
        rows={3}
        maxLength={200}
        showCount
        value={String(raw ?? '')}
        onChange={(e) => setValue(field.key, e.target.value)}
      />
    );
  };

  if (loading || !values) {
    return (
      <Card>
        <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
      </Card>
    );
  }

  return (
    <div>
      <Card
        title="应用配置"
        extra={(
          <Space>
            <Button icon={<UndoOutlined />} onClick={resetAll}>恢复默认</Button>
            <Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>
          </Space>
        )}
      >
        <Paragraph type="secondary" style={{ marginBottom: 20 }}>
          配置保存后<Text strong> 立即生效</Text>，无需重启服务。
        </Paragraph>

        <Row gutter={[16, 16]}>
          {cards.map((card) => (
            <Col key={card.key} xs={24} md={12} xl={8}>
              <Card
                size="small"
                title={(
                  <Space>
                    {card.label}
                    {/* 每张卡片的主开关状态直接外显，不必展开就能看清全局 */}
                    {card.fields[0]?.kind === 'switch' && (
                      <Tag color={values[card.fields[0].key] ? 'green' : 'default'}>
                        {values[card.fields[0].key] ? '已启用' : '已关闭'}
                      </Tag>
                    )}
                  </Space>
                )}
                extra={(
                  <Button
                    type="link"
                    size="small"
                    loading={saving === card.key}
                    onClick={() => void saveCard(card)}
                  >
                    保存
                  </Button>
                )}
              >
                <div style={{ minHeight: 92 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>{card.desc}</Text>
                  <div style={{ marginTop: 12, display: 'grid', gap: 12 }}>
                    {card.fields.map((f) => (
                      <div key={f.key}>
                        <div style={{ marginBottom: 4, fontSize: 13 }}>{f.label}</div>
                        {renderField(f)}
                      </div>
                    ))}
                  </div>
                </div>
              </Card>
            </Col>
          ))}
        </Row>
      </Card>
    </div>
  );
}
