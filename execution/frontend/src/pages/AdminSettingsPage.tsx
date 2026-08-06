import { useEffect, useState } from 'react'
import { Alert, Button, Card, Form, Input, Select, Typography, message } from 'antd'
import { getSettings, updateSettings } from '../api'

export default function AdminSettingsPage() {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  // Обязательная находка №2: _errors — диагностика вида
  // { имя_настройки: текст_ошибки }, а не обычное строковое поле формы.
  // Она появляется в ответе GET (_current_settings, app/api/admin_settings.py),
  // когда SettingsService.get_secret не может расшифровать секрет текущим
  // ENCRYPTION_KEY (реалистичный сценарий — ключ шифрования сменили в .env,
  // а секрет в БД остался зашифрован прежним). Держим её отдельно от полей
  // формы: form.setFieldsValue({ ...v }) без разбора положило бы _errors
  // как несвязанное с реальными Form.Item значение — оно бы просто молча
  // терялось, а админ не узнал бы, что ключ битый и его нужно ввести заново.
  const [errors, setErrors] = useState<Record<string, string> | null>(null)

  useEffect(() => {
    getSettings().then(({ _errors, ...values }) => {
      form.setFieldsValue({ ...values, routerai_api_key: '' })
      setErrors(_errors && Object.keys(_errors).length > 0 ? _errors : null)
    })
  }, [])

  const submit = async (values: Record<string, string>) => {
    setLoading(true)
    try {
      const { _errors, ...saved } = await updateSettings(values)
      form.setFieldsValue({ ...saved, routerai_api_key: '' })
      setErrors(_errors && Object.keys(_errors).length > 0 ? _errors : null)
      message.success('Настройки сохранены')
    } catch {
      // Устоявшийся в проекте паттерн (Task 21-24): onFinish не перехватывается
      // самой antd-формой — без catch отказ updateSettings (например, 422 на
      // невалидном int-поле) стал бы unhandled rejection, хотя текст ошибки
      // уже показан глобальным интерцептором api.ts.
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <Typography.Title level={4} style={{ marginTop: 0 }}>Настройки RouterAI</Typography.Title>

      {errors && (
        <Alert
          type="warning" showIcon style={{ maxWidth: 620, marginBottom: 16 }}
          message="Не удалось расшифровать часть секретных настроек"
          description={
            <>
              Скорее всего сменился ENCRYPTION_KEY в окружении сервера, а секрет
              в базе остался зашифрован прежним ключом. Ниже перечисленные поля
              нужно ввести заново и сохранить.
              <ul style={{ margin: '8px 0 0', paddingLeft: 20 }}>
                {Object.entries(errors).map(([key, text]) => (
                  <li key={key}><b>{key}</b>: {text}</li>
                ))}
              </ul>
            </>
          }
        />
      )}

      <Card style={{ maxWidth: 620 }}>
        <Form form={form} layout="vertical" onFinish={submit} requiredMark={false}>
          <Form.Item name="routerai_base_url" label="Базовый URL">
            <Input />
          </Form.Item>
          <Form.Item name="routerai_api_key" label="Ключ API"
                     extra="Пусто — оставить текущий ключ">
            <Input.Password placeholder="не отображается" />
          </Form.Item>
          <Form.Item name="text_model" label="Модель для текста">
            <Input />
          </Form.Item>
          <Form.Item name="image_model" label="Модель для картинок">
            <Input />
          </Form.Item>
          <Form.Item name="image_quality" label="Качество картинок"
                     extra="high дороже примерно втрое: ≈16.8 против ≈5.4 за кадр">
            <Select options={[{ value: 'medium', label: 'medium' },
                              { value: 'high', label: 'high' }]} />
          </Form.Item>
          <Form.Item name="image_size" label="Размер генерации">
            <Select options={[{ value: '1536x1024', label: '1536×1024' },
                              { value: '1024x1024', label: '1024×1024' },
                              { value: '1024x1536', label: '1024×1536' }]} />
          </Form.Item>
          <Form.Item name="image_workers" label="Параллельных генераций">
            <Input />
          </Form.Item>
          <Form.Item name="llm_max_retries" label="Повторов при сбое">
            <Input />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={loading}>Сохранить</Button>
        </Form>
      </Card>
    </>
  )
}
