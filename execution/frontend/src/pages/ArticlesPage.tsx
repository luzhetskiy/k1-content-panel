import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Button, Card, Form, InputNumber, Modal, Select, Space, Table, Tag, Typography,
} from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { Batch, SiteBrief, createBatch, getBatches, getSites } from '../api'

const STATUS: Record<string, { color: string; label: string }> = {
  topics_pending: { color: 'processing', label: 'Подбираются темы' },
  topics_review: { color: 'warning', label: 'Темы на согласовании' },
  running: { color: 'processing', label: 'Генерируется' },
  done: { color: 'success', label: 'Готово' },
  failed: { color: 'error', label: 'Ошибка' },
}

// Находка Task 22, п.1: «по {N} картинки» верно только для 2-4 — для 1 нужно
// «картинка», для 5+ и для 11-14 — «картинок». reference_images приходит из
// синхронизации эталонной статьи сайта и может быть любым числом, поэтому
// склонение считаем по стандартному правилу русского языка, а не полагаемся
// на форму по умолчанию.
function pluralizeImages(n: number): string {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return 'картинка'
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'картинки'
  return 'картинок'
}

export default function ArticlesPage() {
  const navigate = useNavigate()
  const [batches, setBatches] = useState<Batch[]>([])
  const [sites, setSites] = useState<SiteBrief[]>([])
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()

  const load = () => getBatches().then(setBatches)

  useEffect(() => {
    load()
    getSites().then(setSites)
  }, [])

  useEffect(() => {
    // Поллинг только пока есть незавершённые партии — иначе сервер опрашивается
    // впустую весь рабочий день.
    const active = batches.some(b => ['topics_pending', 'running'].includes(b.status))
    if (!active) return
    const timer = setInterval(load, 5000)
    return () => clearInterval(timer)
  }, [batches])

  const submit = async (values: { site_id: number; count: number }) => {
    try {
      const batch = await createBatch(values.site_id, values.count)
      setOpen(false)
      form.resetFields()
      navigate(`/articles/${batch.id}`)
    } catch {
      // Находка Task 22, п.2 — тот же класс проблемы, что и в LoginPage.tsx
      // (Task 21): antd Form не ждёt и не перехватывает промис из onFinish,
      // без catch отказ createBatch (например, случайный 500 или обрыв сети)
      // стал бы unhandled rejection в консоли браузера. Сообщение об ошибке
      // уже показывает интерцептор api.ts — здесь catch нужен только чтобы
      // погасить промис; модалка остаётся открытой с заполненной формой, что
      // само по себе разумное поведение при ошибке.
    }
  }

  const selectedSiteId = Form.useWatch('site_id', form)
  const selectedSite = sites.find(s => s.id === selectedSiteId)

  return (
    <>
      <Space style={{ marginBottom: 16, justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Партии статей</Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
          Новая партия
        </Button>
      </Space>

      <Card styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="id"
          dataSource={batches}
          pagination={{ pageSize: 20 }}
          onRow={r => ({ onClick: () => navigate(`/articles/${r.id}`),
                         style: { cursor: 'pointer' } })}
          columns={[
            { title: 'Сайт', dataIndex: 'site_name' },
            { title: 'Статей', dataIndex: 'requested_count', width: 90 },
            {
              title: 'Готово', width: 110,
              render: (_, r: Batch) =>
                `${r.articles.filter(a => a.status === 'published').length} / ${r.articles.length}`,
            },
            {
              title: 'Статус', dataIndex: 'status', width: 200,
              render: (s: string) => (
                <Tag color={STATUS[s]?.color}>{STATUS[s]?.label ?? s}</Tag>
              ),
            },
            {
              title: 'Создана', dataIndex: 'created_at', width: 160,
              render: (v: string) => dayjs(v).format('DD.MM.YYYY HH:mm'),
            },
          ]}
        />
      </Card>

      <Modal open={open} onCancel={() => setOpen(false)} onOk={form.submit}
             title="Новая партия статей" okText="Подобрать темы" destroyOnHidden>
        <Form form={form} layout="vertical" onFinish={submit}
              initialValues={{ count: 5 }} requiredMark={false}>
          <Form.Item name="site_id" label="Сайт"
                     rules={[{ required: true, message: 'Выберите сайт' }]}>
            <Select
              placeholder="Выберите сайт"
              options={sites.map(s => ({ value: s.id, label: `${s.name} — ${s.domain}` }))}
            />
          </Form.Item>
          <Form.Item name="count" label="Сколько статей"
                     rules={[{ required: true, message: 'Укажите количество' }]}>
            <InputNumber min={1} max={50} style={{ width: '100%' }} />
          </Form.Item>
          {/* Домен и раздел показываются до запуска: при десятке сайтов промах —
              самая вероятная авария, а всё создаётся черновиком именно там. */}
          {selectedSite && selectedSite.is_ready && (
            <div style={{ color: '#71717a', fontSize: 13 }}>
              Черновики будут созданы на <b>{selectedSite.domain}</b> в разделе{' '}
              <b>{selectedSite.url_prefix}</b>, по {selectedSite.reference_images}{' '}
              {pluralizeImages(selectedSite.reference_images)} в статье (столько же,
              сколько в эталонной статье сайта).
            </div>
          )}
          {selectedSite && !selectedSite.is_ready && (
            <Alert
              type="warning" showIcon
              message="Сайт не готов к генерации"
              description="Эталонная статья не синхронизирована. Попроси администратора
                           нажать «Проверить и синхронизировать» на карточке сайта."
            />
          )}
        </Form>
      </Modal>
    </>
  )
}
