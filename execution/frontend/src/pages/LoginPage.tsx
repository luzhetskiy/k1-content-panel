import { useState } from 'react'
import { Button, Card, Form, Input, Typography } from 'antd'
import { login } from '../api'
import { useAuth } from '../auth'

export default function LoginPage() {
  const { setProfile } = useAuth()
  const [loading, setLoading] = useState(false)

  const onFinish = async (values: { email: string; password: string }) => {
    setLoading(true)
    try {
      const profile = await login(values.email, values.password)
      setProfile(profile)
      location.href = '/articles'
    } catch {
      // Ничего не показываем здесь намеренно: интерцептор api.ts уже вывел
      // понятный тост («неверный email или пароль» от бэкенда) для этого же
      // запроса — login() помечен как обычный, не «тихий» вызов (см. api.ts).
      // catch здесь нужен не для UI, а чтобы promise из onFinish не улетал
      // необработанным: antd Form не ждёт и не перехватывает промис,
      // возвращённый onFinish, поэтому без catch отклонение login() стало бы
      // unhandled rejection в консоли браузера при каждом неверном пароле.
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center',
      justifyContent: 'center', background: '#f4f4f5', padding: 16,
    }}>
      <Card style={{ width: 360 }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <div style={{
            width: 44, height: 44, borderRadius: 12, margin: '0 auto 12px',
            background: 'linear-gradient(135deg, #dca34c 0%, #e8b96a 100%)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 18, fontWeight: 700, color: '#fff',
          }}>K1</div>
          <Typography.Title level={4} style={{ margin: 0 }}>Контент-сервис</Typography.Title>
        </div>
        <Form layout="vertical" onFinish={onFinish} requiredMark={false}>
          <Form.Item name="email" label="Email"
                     rules={[{ required: true, message: 'Введите email' }]}>
            <Input autoComplete="username" size="large" />
          </Form.Item>
          <Form.Item name="password" label="Пароль"
                     rules={[{ required: true, message: 'Введите пароль' }]}>
            <Input.Password autoComplete="current-password" size="large" />
          </Form.Item>
          <Button type="primary" htmlType="submit" size="large" block loading={loading}>
            Войти
          </Button>
        </Form>
      </Card>
    </div>
  )
}
