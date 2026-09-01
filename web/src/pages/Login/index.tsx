import { useCallback, useEffect, useState } from 'react';
import { App, Button, Form, Input, Typography } from 'antd';
import { LockOutlined, SafetyCertificateOutlined, UserOutlined } from '@ant-design/icons';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuthStore } from '@/stores/authStore';
import { fetchCaptcha } from '@/services/api';
import styles from './index.module.css';

const { Title, Text } = Typography;

interface LoginFormValues {
  username: string;
  password: string;
  captcha?: string;
}

/**
 * 登录页 —— 还原 demo.html 的 .login-page 视觉规范。
 * 验证码在「连续失败 3 次后」由后端要求，前端动态显示。
 */
export default function LoginPage() {
  const [form] = Form.useForm<LoginFormValues>();
  const [loading, setLoading] = useState(false);
  const [captcha, setCaptcha] = useState<{ id: string; image: string } | null>(null);
  const [needCaptcha, setNeedCaptcha] = useState(false);
  const { login } = useAuthStore();
  const navigate = useNavigate();
  const location = useLocation();
  const { message } = App.useApp();

  // 默认落地页是智能问数（平台主入口），而不是概览页。
  // 注意这里必须给默认值：登录后是 navigate(from)，不会再走路由的 index 重定向。
  const from = (location.state as { from?: string } | null)?.from
    || new URLSearchParams(location.search).get('redirect')
    || '/ai-qa';

  const loadCaptcha = useCallback(async (username: string) => {
    try {
      const res = await fetchCaptcha(username);
      setNeedCaptcha(res.required);
      setCaptcha({ id: res.captcha_id, image: res.image });
    } catch {
      setCaptcha(null);
    }
  }, []);

  useEffect(() => {
    void loadCaptcha(form.getFieldValue('username') || '');
  }, [loadCaptcha, form]);

  const handleSubmit = async (values: LoginFormValues) => {
    setLoading(true);
    try {
      const user = await login(values.username, values.password, values.captcha, captcha?.id);
      message.success(`欢迎回来，${user.nickname || user.username}`);
      navigate(from, { replace: true });
    } catch (err) {
      message.error((err as Error).message);
      // 失败后刷新验证码，并按后端要求决定是否展示
      await loadCaptcha(values.username);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className={styles.page}>
      <div className={styles.left}>
        <div className={styles.bgGrid} />
        {[2, 3, 4, 5, 6].map((n) => (
          <span key={n} className={`${styles.particle} ${styles[`p${n}`]}`} />
        ))}
        <div className={styles.leftContent}>
          <div className={styles.logo}>
            <span className={styles.logoIcon}>★</span>
          </div>
          <Title level={2} className={styles.brandTitle}>经管之星</Title>
          <Text className={styles.slogan}>
            企业级销售经管数据管理平台 · 用一句话问出你要的经营数据
          </Text>
        </div>
      </div>

      <div className={styles.right}>
        <div className={styles.formWrap}>
          <Title level={3} className={styles.formTitle}>欢迎登录</Title>
          <Text type="secondary" className={styles.formSub}>请使用您的经管平台账号登录</Text>

          <Form<LoginFormValues>
            form={form}
            layout="vertical"
            size="large"
            onFinish={handleSubmit}
            initialValues={{ username: 'admin', password: '123456' }}
            requiredMark={false}
          >
            <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
              <Input prefix={<UserOutlined />} placeholder="请输入用户名" autoComplete="username" />
            </Form.Item>

            <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
              <Input.Password
                prefix={<LockOutlined />}
                placeholder="请输入密码"
                autoComplete="current-password"
                onPressEnter={() => form.submit()}
              />
            </Form.Item>

            {needCaptcha && (
              <Form.Item name="captcha" label="验证码" rules={[{ required: true, message: '请输入验证码' }]}>
                <div className={styles.captchaRow}>
                  <Input prefix={<SafetyCertificateOutlined />} placeholder="请输入验证码" />
                  {captcha ? (
                    <img
                      src={captcha.image}
                      alt="验证码"
                      className={styles.captchaImg}
                      onClick={() => loadCaptcha(form.getFieldValue('username') || '')}
                      title="点击刷新"
                    />
                  ) : (
                    <Button onClick={() => loadCaptcha(form.getFieldValue('username') || '')}>获取</Button>
                  )}
                </div>
              </Form.Item>
            )}

            <Button type="primary" htmlType="submit" block loading={loading} className={styles.submit}>
              登录
            </Button>
          </Form>

          <div className={styles.demoTip}>
            演示账号：admin / zhangsan / huangjiu，密码均为 123456
          </div>
          <div className={styles.footer}>经管之星 v1.0.0 · © 2026</div>
        </div>
      </div>
    </div>
  );
}
