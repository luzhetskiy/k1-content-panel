import { useEffect, useState } from 'react'
import { Card, Space, Statistic, Table, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import { JobRow, getJobs } from '../api'

const KIND: Record<string, string> = {
  generate_topics: 'Подбор тем',
  run_batch: 'Генерация партии',
  retry_article: 'Повтор статьи',
}

export default function JobsPage() {
  const [jobs, setJobs] = useState<JobRow[]>([])

  useEffect(() => {
    getJobs().then(setJobs)
    // Лёгкий пункт плана: фоновый опрос без .catch() при обрыве сети даёт
    // отдельный тост «Ошибка сервера» через глобальный интерцептор (api.ts)
    // на каждый неудачный запрос — то есть каждые 10 секунд, пока сеть не
    // восстановится. Решение: НЕ чинить здесь тем же приёмом, что и
    // silentAuthCheck у me() (Task 21). Там 401 — ОЖИДАЕМЫЙ ответ на каждый
    // вызов (это и есть сама проверка сессии), поэтому тост нужно было
    // подавлять всегда. Здесь же ошибка НЕ ожидаема — это реальный сбой сети
    // или сервера, о котором пользователь должен узнать; просто он узнает об
    // этом несколько раз подряд, если разрыв длится дольше 10 секунд. Тосты
    // antd самостоятельно исчезают через несколько секунд и не блокируют
    // работу со страницей, а тот же самый непокрытый catch уже есть в
    // ArticlesPage.tsx и BatchPage.tsx (их поллинг вне Files этой задачи —
    // не трогаю). Ради единообразия между тремя похожими местами оставляю
    // как есть и фиксирую это решение здесь, а не молча расхожусь с уже
    // принятым для двух других экранов поведением.
    const timer = setInterval(() => getJobs().then(setJobs), 10000)
    return () => clearInterval(timer)
  }, [])

  const running = jobs.filter(j => j.status === 'running').length
  const totalCost = jobs.reduce((sum, j) => sum + j.cost, 0)

  return (
    <>
      <Typography.Title level={4} style={{ marginTop: 0 }}>Журнал задач</Typography.Title>

      <Space style={{ marginBottom: 16 }} size={16}>
        <Card size="small" style={{ minWidth: 180 }}>
          <Statistic title="Выполняется сейчас" value={running} />
        </Card>
        <Card size="small" style={{ minWidth: 180 }}>
          <Statistic title="Расход RouterAI" value={totalCost} precision={1} />
        </Card>
      </Space>

      <Card styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="id" dataSource={jobs} pagination={{ pageSize: 20 }}
          columns={[
            { title: 'Задача', dataIndex: 'kind', render: (k: string) => KIND[k] ?? k },
            { title: 'Сайт', dataIndex: 'site_name' },
            {
              title: 'Статус', dataIndex: 'status', width: 130,
              render: (s: string) => (
                <Tag color={s === 'ok' ? 'success' : s === 'failed' ? 'error' : 'processing'}>
                  {s === 'ok' ? 'готово' : s === 'failed' ? 'ошибка' : 'выполняется'}
                </Tag>
              ),
            },
            { title: 'Итог', dataIndex: 'log_text' },
            { title: 'Токены', dataIndex: 'tokens_total', width: 100 },
            {
              title: 'Стоимость', dataIndex: 'cost', width: 110,
              render: (v: number) => v.toFixed(1),
            },
            {
              title: 'Начата', dataIndex: 'started_at', width: 150,
              render: (v: string) => dayjs(v).format('DD.MM HH:mm:ss'),
            },
          ]}
        />
      </Card>
    </>
  )
}
