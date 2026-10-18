import { useEffect, useState } from 'react'
import dayjs from 'dayjs'
import { Alert, Button, Col, Drawer, Popconfirm, Row, Space, Table, Tag, Typography, message } from 'antd'
import { LinkOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  HouseProjectPageDetail, HouseProjectPageRow, getHouseProjectPage, getHouseProjectPages,
  rewriteHouseProjectPage,
} from '../api'
import { HOUSE_PROJECT_PAGE_STATUS } from '../statuses'

function StatusTag({ row }: { row: HouseProjectPageRow }) {
  if (row.stuck) return <Tag color="error">Похоже, зависла</Tag>
  const status = HOUSE_PROJECT_PAGE_STATUS[row.status]
  return <Tag color={status?.color}>{status?.label ?? row.status}</Tag>
}

// Описание — HTML с p/h3/ul; в карточке показываем текстом, без innerHTML.
function blocks(html: string): { heading: boolean; item: boolean; text: string }[] {
  return html.split(/(?=<(?:p|h3|li)\b)/i)
    .map(part => ({
      heading: /^<h3\b/i.test(part),
      item: /^<li\b/i.test(part),
      text: part.replace(/<br\s*\/?>/gi, ' ').replace(/<[^>]+>/g, ' ')
        .replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ').trim(),
    }))
    .filter(block => block.text)
}

function Description({ title, html, chars }: { title: string; html: string; chars: number }) {
  return (
    <>
      <Typography.Title level={5}>{title} · {chars} символов</Typography.Title>
      {html
        ? blocks(html).map((block, i) => (
          <Typography.Paragraph key={i} strong={block.heading} style={{ marginBottom: 8 }}>
            {block.item ? `• ${block.text}` : block.text}
          </Typography.Paragraph>
        ))
        : <Typography.Text type="secondary">—</Typography.Text>}
    </>
  )
}

function PageDrawer({ pageId, busy, onClose, onChanged }: {
  pageId: number | null
  busy: boolean
  onClose: () => void
  onChanged: () => void
}) {
  const [page, setPage] = useState<HouseProjectPageDetail | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setPage(null)
    if (pageId) getHouseProjectPage(pageId).then(setPage)
  }, [pageId])

  if (!pageId) return null

  const canRewrite = page !== null && !busy
    && (['new', 'done', 'failed'].includes(page.status) || page.stuck)

  const rewrite = async () => {
    if (!page) return
    setSaving(true)
    try {
      await rewriteHouseProjectPage(page.id)
      message.success('Рерайт страницы поставлен в очередь')
      onChanged()
      onClose()
    } catch { /* сообщение уже показал интерцептор */ }
    finally { setSaving(false) }
  }

  return (
    <Drawer open width={900} title={page?.title ?? 'Загрузка…'} onClose={onClose}
            extra={canRewrite && (
              <Popconfirm title="Переписать описание заново?"
                          description="Рерайт делается от оригинала, новый текст сразу запишется на сайт."
                          onConfirm={rewrite}>
                <Button icon={<ReloadOutlined />} loading={saving}>Переписать</Button>
              </Popconfirm>
            )}>
      {page && (
        <>
          <Typography.Paragraph type="secondary">
            <a href={page.page_url} target="_blank" rel="noreferrer">{page.url}</a>
            {page.rewritten_at && <> · переписана {dayjs(page.rewritten_at).format('DD.MM.YYYY HH:mm')}
              {page.rewrite_count > 1 && <>, всего раз: {page.rewrite_count}</>}</>}
          </Typography.Paragraph>
          {page.skip_reason && (
            <Alert type="info" showIcon style={{ marginBottom: 16 }} message={`Пропущена: ${page.skip_reason}`} />
          )}
          {page.error_text && (
            <Alert type="error" showIcon style={{ marginBottom: 16 }} message="Ошибка" description={page.error_text} />
          )}
          <Row gutter={24}>
            <Col xs={24} md={12}>
              <Description title="Оригинал" html={page.original_html} chars={page.original_chars} />
            </Col>
            <Col xs={24} md={12}>
              <Description title="Сейчас на сайте" html={page.rewritten_html} chars={page.rewritten_chars} />
            </Col>
          </Row>
        </>
      )}
    </Drawer>
  )
}

export default function HouseProjectPages({ siteId, busy, onChanged }: {
  siteId: number
  busy: boolean
  onChanged: () => void
}) {
  const [rows, setRows] = useState<HouseProjectPageRow[] | null>(null)
  const [openId, setOpenId] = useState<number | null>(null)

  const load = () => getHouseProjectPages(siteId).then(setRows)

  useEffect(() => { load() }, [siteId])

  useEffect(() => {
    const working = busy || (rows ?? []).some(r => r.status === 'queued' || r.status === 'in_work')
    if (!working) return
    const timer = setInterval(load, 10000)
    return () => clearInterval(timer)
  }, [rows, busy])

  if (rows === null) return <Typography.Text type="secondary">Загрузка…</Typography.Text>
  if (!rows.length) {
    return <Typography.Text type="secondary">Страниц пока нет — нажмите «Запустить рерайт»</Typography.Text>
  }

  return (
    <>
      <Table<HouseProjectPageRow>
        rowKey="id"
        size="small"
        dataSource={rows}
        pagination={false}
        columns={[
          {
            title: 'Страница', dataIndex: 'title',
            render: (title: string, r) => (
              <Space size={4}>
                <Button type="link" style={{ padding: 0, height: 'auto', whiteSpace: 'normal', textAlign: 'left' }}
                        onClick={() => setOpenId(r.id)}>
                  {title}
                </Button>
                {r.page_url && (
                  <a href={r.page_url} target="_blank" rel="noreferrer"><LinkOutlined /></a>
                )}
              </Space>
            ),
          },
          { title: 'Статус', width: 170, render: (_, r) => <StatusTag row={r} /> },
          {
            title: 'Рерайт', width: 150,
            render: (_, r) => r.rewritten_at ? dayjs(r.rewritten_at).format('DD.MM.YYYY HH:mm') : '—',
          },
          { title: 'Раз', width: 60, dataIndex: 'rewrite_count' },
        ]}
      />
      <PageDrawer pageId={openId} busy={busy} onClose={() => setOpenId(null)}
                  onChanged={() => { load(); onChanged() }} />
    </>
  )
}
