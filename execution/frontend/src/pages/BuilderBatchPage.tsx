import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Alert, Button, Card, Popconfirm, Space, Table, Tag, Typography, message } from 'antd'
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  CompanyBatchRow, CompanyRow, addNextBatchCompany, getCompanyBatch, removeBatchCompany,
  retryCompany, runCompanyBatch,
} from '../api'

const COMPANY_STATUS: Record<string, { color: string; label: string }> = {
  draft: { color: 'default', label: 'Ожидает' },
  generating: { color: 'processing', label: 'Собирается' },
  published: { color: 'success', label: 'Опубликован черновик' },
  failed: { color: 'error', label: 'Ошибка' },
}

export default function BuilderBatchPage() {
  const { id } = useParams()
  const batchId = Number(id)
  const [batch, setBatch] = useState<CompanyBatchRow | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => getCompanyBatch(batchId).then(setBatch)

  useEffect(() => { load() }, [batchId])

  useEffect(() => {
    if (!batch) return
    const active = batch.status === 'running'
      || batch.companies.some(c => c.status === 'generating')
    if (!active) return
    const timer = setInterval(load, 5000)
    return () => clearInterval(timer)
  }, [batch])

  if (!batch) return null

  const removeCompany = async (companyId: number) => {
    setBusy(true)
    try { setBatch(await removeBatchCompany(batchId, companyId)) } finally { setBusy(false) }
  }

  const addNext = async () => {
    setBusy(true)
    try { setBatch(await addNextBatchCompany(batchId)) }
    catch { /* сообщение уже показал интерцептор */ }
    finally { setBusy(false) }
  }

  const run = async () => {
    setBusy(true)
    try {
      setBatch(await runCompanyBatch(batchId))
      message.success('Запущено — сборка страниц пойдёт в фоне')
    } finally { setBusy(false) }
  }

  const editable = batch.status === 'selection_review'

  return (
    <>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        Партия №{batch.id} — {batch.site_name}
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        {batch.region_raw} · {batch.category_raw} → «{batch.category_normalized}»
      </Typography.Paragraph>

      {batch.error_text && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }}
               message="Партия завершилась с ошибкой" description={batch.error_text} />
      )}

      {editable ? (
        <Card title="Отобранные компании" extra={
          <Space>
            <Button icon={<PlusOutlined />} disabled={busy} onClick={addNext}>
              Добрать ещё
            </Button>
            <Button type="primary" loading={busy}
                    disabled={busy || batch.companies.length === 0} onClick={run}>
              Запустить генерацию
            </Button>
          </Space>
        }>
          <Table
            rowKey="id"
            dataSource={batch.companies}
            pagination={false}
            columns={[
              { title: 'Компания', dataIndex: 'name' },
              { title: 'Сайт', dataIndex: 'website' },
              { title: 'Отзывов', dataIndex: 'reviews_count', width: 100 },
              { title: 'Рейтинг', dataIndex: 'rating', width: 100 },
              {
                title: '', width: 60,
                render: (_, r: CompanyRow) => (
                  <Popconfirm title="Убрать компанию из партии?"
                              onConfirm={() => removeCompany(r.id)}>
                    <Button type="text" icon={<DeleteOutlined />} disabled={busy} />
                  </Popconfirm>
                ),
              },
            ]}
          />
        </Card>
      ) : (
        <Card styles={{ body: { padding: 0 } }}>
          <Table
            rowKey="id"
            dataSource={batch.companies}
            pagination={false}
            columns={[
              { title: 'Компания', dataIndex: 'name' },
              {
                title: 'Статус', dataIndex: 'status', width: 220,
                render: (s: string) => (
                  <Tag color={COMPANY_STATUS[s]?.color}>{COMPANY_STATUS[s]?.label ?? s}</Tag>
                ),
              },
              {
                title: 'Черновик', width: 140,
                render: (_, r: CompanyRow) => r.remote_url
                  ? <a href={r.remote_url} target="_blank" rel="noreferrer">открыть</a>
                  : '—',
              },
              {
                title: '', width: 60,
                render: (_, r: CompanyRow) => r.status === 'failed' || r.status === 'published' ? (
                  <Popconfirm title={r.status === 'published'
                    ? 'Пересобрать страницу и тизер этой компании?'
                    : 'Повторить сборку этой компании?'}
                              onConfirm={async () => { await retryCompany(r.id); load() }}>
                    <Button type="text" icon={<ReloadOutlined />} />
                  </Popconfirm>
                ) : null,
              },
            ]}
            expandable={{
              expandedRowRender: (r: CompanyRow) => (
                <Typography.Text type="danger">{r.error_text}</Typography.Text>
              ),
              rowExpandable: (r: CompanyRow) => Boolean(r.error_text),
            }}
          />
        </Card>
      )}
    </>
  )
}
