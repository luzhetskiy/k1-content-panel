import { ReactNode, useEffect, useMemo, useState } from 'react'
import dayjs from 'dayjs'
import {
  Alert, Button, Collapse, Descriptions, Drawer, Popconfirm, Space, Table, Tag, Typography, message,
} from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { CategoryMetaRow, getMetaCategories, regenerateMetaCategory } from '../api'
import { CATEGORY_META_STATUS } from '../statuses'

type TreeRow = CategoryMetaRow & { children?: TreeRow[] }

function buildTree(rows: CategoryMetaRow[]): TreeRow[] {
  const byRemote = new Map<number, TreeRow>()
  rows.forEach(r => byRemote.set(r.remote_id, { ...r }))
  const roots: TreeRow[] = []
  byRemote.forEach(node => {
    const parent = node.remote_parent_id != null ? byRemote.get(node.remote_parent_id) : undefined
    if (parent) {
      if (!parent.children) parent.children = []
      parent.children.push(node)
    } else {
      roots.push(node)
    }
  })
  return roots
}

function StatusTag({ row }: { row: CategoryMetaRow }) {
  if (row.stuck) return <Tag color="error">Похоже, зависла</Tag>
  const status = CATEGORY_META_STATUS[row.status]
  return (
    <Space size={4} wrap>
      <Tag color={status?.color}>{status?.label ?? row.status}</Tag>
      {row.status === 'done' && row.low_demand && <Tag color="warning">мало данных</Tag>}
    </Space>
  )
}

// SEO-текст — HTML с h2/p/ul; в карточке показываем текстом, абзацами.
function seoParagraphs(html: string): string[] {
  return html.split(/<\/(?:h2|h3|p|li)>/)
    .map(part => part.replace(/<[^>]+>/g, ' ').replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ').trim())
    .filter(Boolean)
}

function withCount(text: string, max?: number): ReactNode {
  if (!text) return '—'
  return (
    <>
      {text}{' '}
      {max && (
        <Typography.Text type={text.length > max ? 'danger' : 'secondary'} style={{ fontSize: 12 }}>
          ({text.length}/{max})
        </Typography.Text>
      )}
    </>
  )
}

function formText(row: CategoryMetaRow): string {
  if (row.chosen_form === 'nominative') return `без склонения — «${row.form_nominative}»`
  if (row.chosen_form === 'declined') return `со склонением — «${row.form_buy}»`
  return '—'
}

function candidatesText(row: CategoryMetaRow): string {
  if (row.candidates.length < 2) return ''
  const counted = row.candidates.map(c =>
    `«${c.phrase}» ${c.count ?? '—'}${c.same_product === false ? ' (другой товар)' : ''}`)
  return `${counted.join(' · ')} → выбрано «${row.seed_phrase}»`
}

function CategoryDrawer({ row, onClose, onChanged }: {
  row: CategoryMetaRow | null
  onClose: () => void
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  if (!row) return null

  const canRegenerate = Boolean(row.seed_phrase)
    && (['new', 'done', 'failed'].includes(row.status) || row.stuck)

  const regenerate = async () => {
    setBusy(true)
    try {
      await regenerateMetaCategory(row.id)
      message.success('Перегенерация поставлена в очередь')
      onChanged()
    } catch { /* сообщение уже показал интерцептор */ }
    finally { setBusy(false) }
  }

  const counts = row.nominative_count != null
    ? ` · «купить ${row.form_nominative}» ${row.nominative_count}, «${row.form_buy}» ${row.declined_count}`
    : ''
  const previous = row.previous_json && Object.keys(row.previous_json).length > 0
    ? row.previous_json : null

  return (
    <Drawer open width={600} title={row.name} onClose={onClose}
            extra={canRegenerate && (
              <Popconfirm title="Перегенерировать теги и SEO-текст категории?"
                          description="Новые теги и текст сразу запишутся на сайт."
                          onConfirm={regenerate}>
                <Button icon={<ReloadOutlined />} loading={busy}>Перегенерировать</Button>
              </Popconfirm>
            )}>
      <Typography.Paragraph type="secondary">
        {row.path}{row.page_url && <> · <a href={row.page_url} target="_blank" rel="noreferrer">страница</a></>}
      </Typography.Paragraph>
      {row.skip_reason && (
        <Alert type="info" showIcon style={{ marginBottom: 16 }} message={`Пропущена: ${row.skip_reason}`} />
      )}
      {row.error_text && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }} message="Ошибка" description={row.error_text} />
      )}
      <Descriptions title="Теги" column={1} size="small" bordered>
        <Descriptions.Item label="title">{withCount(row.title, 70)}</Descriptions.Item>
        <Descriptions.Item label="h1">{withCount(row.h1, 60)}</Descriptions.Item>
        <Descriptions.Item label="description">{withCount(row.meta_description, 170)}</Descriptions.Item>
        <Descriptions.Item label="keywords">{withCount(row.meta_keywords)}</Descriptions.Item>
        <Descriptions.Item label="ai_keywords">{withCount(row.ai_keywords)}</Descriptions.Item>
      </Descriptions>
      <Collapse size="small" style={{ marginTop: 16 }} items={[{
        key: 'seo',
        label: row.seo_text
          ? `SEO-текст · ${seoParagraphs(row.seo_text).join(' ').length} символов`
          : 'SEO-текст · ещё не написан',
        children: row.seo_text
          ? seoParagraphs(row.seo_text).map((part, i) => (
            <Typography.Paragraph key={i} style={{ marginBottom: 8 }}>{part}</Typography.Paragraph>
          ))
          : '—',
      }]} />
      <Descriptions title="Wordstat" column={1} size="small" bordered style={{ marginTop: 16 }}>
        <Descriptions.Item label="Фраза">{row.seed_phrase || '—'}</Descriptions.Item>
        {candidatesText(row) && (
          <Descriptions.Item label="Варианты названия">{candidatesText(row)}</Descriptions.Item>
        )}
        <Descriptions.Item label="Форма">{formText(row)}{counts}</Descriptions.Item>
        <Descriptions.Item label="Запросов за 30 дней">{row.total_count ?? '—'}</Descriptions.Item>
      </Descriptions>
      {previous && (
        <Collapse style={{ marginTop: 16 }} items={[{
          key: 'previous', label: 'Было на сайте до нас',
          children: (
            <Descriptions column={1} size="small">
              {Object.entries(previous).map(([key, value]) => (
                <Descriptions.Item key={key} label={key}>{value || '—'}</Descriptions.Item>
              ))}
            </Descriptions>
          ),
        }]} />
      )}
    </Drawer>
  )
}

export default function CategoryMetaTree({ siteId, active }: { siteId: number; active: boolean }) {
  const [rows, setRows] = useState<CategoryMetaRow[] | null>(null)
  const [openId, setOpenId] = useState<number | null>(null)

  const load = () => getMetaCategories(siteId).then(setRows)

  useEffect(() => { load() }, [siteId])

  useEffect(() => {
    const busy = active || (rows ?? []).some(r => r.status === 'queued' || r.status === 'in_work')
    if (!busy) return
    const timer = setInterval(load, 10000)
    return () => clearInterval(timer)
  }, [rows, active])

  const tree = useMemo(() => buildTree(rows ?? []), [rows])

  if (rows === null) return <Typography.Text type="secondary">Загрузка…</Typography.Text>
  if (!rows.length) {
    return <Typography.Text type="secondary">Категорий пока нет — нажмите «Обновить метатеги»</Typography.Text>
  }

  return (
    <>
      <Table<TreeRow>
        rowKey="id"
        size="small"
        dataSource={tree}
        pagination={false}
        columns={[
          {
            title: 'Категория', dataIndex: 'name',
            render: (name: string, r) => (
              <Button type="link" style={{ padding: 0, height: 'auto' }} onClick={() => setOpenId(r.id)}>
                {name}
              </Button>
            ),
          },
          { title: 'Статус', width: 240, render: (_, r) => <StatusTag row={r} /> },
          {
            title: 'Обновлено', width: 150,
            render: (_, r) => r.status === 'done' ? dayjs(r.updated_at).format('DD.MM.YYYY HH:mm') : '—',
          },
        ]}
      />
      <CategoryDrawer row={rows.find(r => r.id === openId) ?? null}
                      onClose={() => setOpenId(null)} onChanged={load} />
    </>
  )
}
