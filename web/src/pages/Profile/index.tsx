import { useState } from 'react';
import { App, Button, Card, Descriptions, Form, Input, Space, Tag, Typography } from 'antd';
import { useAuthStore } from '@/stores/authStore';
import { changePassword } from '@/services/api';

const { Title, Text } = Typography;

export default function Profile() {
  const { user, logout } = useAuthStore();
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm();

  const submit = async () => {
    const values = await form.validateFields();
    if (values.new_password !== values.confirm_password) {
      message.error('两次输入的新密码不一致');
      return;
    }
    setLoading(true);
    try {
      await changePassword(values.old_password, values.new_password);
      message.success('密码已修改，请重新登录');
      form.resetFields();
      await logout();
      location.href = '/login';
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  if (!user) return null;

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card title="个人信息">
        <Descriptions bordered column={2} size="middle">
          <Descriptions.Item label="用户名">{user.username}</Descriptions.Item>
          <Descriptions.Item label="昵称">{user.nickname || '-'}</Descriptions.Item>
          <Descriptions.Item label="手机号">{user.phone || '-'}</Descriptions.Item>
          <Descriptions.Item label="邮箱">{user.email || '-'}</Descriptions.Item>
          <Descriptions.Item label="角色" span={2}>
            {user.role_codes.map((c) => <Tag color="blue" key={c}>{c}</Tag>)}
          </Descriptions.Item>
          <Descriptions.Item label="权限数">{user.perms.length}</Descriptions.Item>
          <Descriptions.Item label="菜单数">{user.menus.length}</Descriptions.Item>
          <Descriptions.Item label="数据权限" span={2}>
            {Object.keys(user.data_perms).length === 0 ? (
              <Text type="secondary">不限制（可见全部经营单元）</Text>
            ) : (
              Object.entries(user.data_perms).map(([menuId, units]) => (
                <div key={menuId}>
                  菜单 #{menuId}：
                  {units && units.length
                    ? units.map((u) => <Tag key={u}>{u}</Tag>)
                    : <Text type="secondary">不限制</Text>}
                </div>
              ))
            )}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="修改密码">
        <Form form={form} layout="vertical" style={{ maxWidth: 420 }} onFinish={submit}>
          <Form.Item name="old_password" label="原密码" rules={[{ required: true, message: '请输入原密码' }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 8, message: '至少 8 位' },
              {
                validator: (_, v) =>
                  !v || (/\d/.test(v) && /[a-zA-Z]/.test(v))
                    ? Promise.resolve()
                    : Promise.reject(new Error('需同时包含字母和数字')),
              },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            name="confirm_password"
            label="确认新密码"
            dependencies={['new_password']}
            rules={[{ required: true, message: '请再次输入新密码' }]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={loading}>修改密码</Button>
        </Form>
        <Text type="secondary" style={{ display: 'block', marginTop: 12 }}>
          修改成功后需要重新登录。
        </Text>
      </Card>
    </Space>
  );
}
