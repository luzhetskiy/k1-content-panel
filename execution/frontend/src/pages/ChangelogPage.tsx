import { useMemo, useState } from 'react'
import { Card, Empty, Segmented, Space, Tag, Typography } from 'antd'
import dayjs from 'dayjs'
import { CHANGELOG, ChangeKind, KIND_COLOR, KIND_LABEL } from '../changelog'

type Filter = 'all' | ChangeKind

const FILTERS: { value: Filter; label: string }[] = [
  { value: 'all', label: 'Всё' },
  { value: 'feature', label: KIND_LABEL.feature },
  { value: 'fix', label: KIND_LABEL.fix },
  { value: 'batch', label: KIND_LABEL.batch },
]

export default function ChangelogPage() {
  const [filter, setFilter] = useState<Filter>('all')

  // Порядок записей задан в changelog.ts и на экране не пересортировывается:
  // за один день выходит несколько доработок, и внутри дня осмысленный
  // порядок — тот, в котором их перечислил автор, а не случайный.
  const groups = useMemo(() => {
    const visible = filter === 'all'
      ? CHANGELOG
      : CHANGELOG.filter(e => e.kind === filter)
    const byDate: { date: string; entries: typeof CHANGELOG }[] = []
    for (const entry of visible) {
      const last = byDate[byDate.length - 1]
      if (last && last.date === entry.date) last.entries.push(entry)
      else byDate.push({ date: entry.date, entries: [entry] })
    }
    return byDate
  }, [filter])

  return (
    <>
      <Typography.Title level={4} style={{ marginTop: 0 }}>Доработки</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ maxWidth: 680 }}>
        Что добавили и что исправили в панели. Записи с меткой «{KIND_LABEL.batch}» —
        разовые работы по уже собранным компаниям и опубликованным страницам:
        они применяются сами, пересобирать партии не нужно.
      </Typography.Paragraph>

      <Segmented<Filter>
        options={FILTERS} value={filter} onChange={setFilter}
        style={{ marginBottom: 20 }}
      />

      {groups.length === 0 && <Empty description="Пока нет записей" />}

      {groups.map(group => (
        <div key={group.date} style={{ marginBottom: 20 }}>
          <Typography.Text style={{
            fontSize: 12, fontWeight: 600, color: '#71717a',
            textTransform: 'uppercase', letterSpacing: '0.04em',
          }}>
            {dayjs(group.date).format('D MMMM YYYY')}
          </Typography.Text>

          <Space direction="vertical" size={10} style={{ display: 'flex', marginTop: 8 }}>
            {group.entries.map(entry => (
              <Card key={entry.title} size="small">
                <div style={{ marginBottom: 6 }}>
                  <Tag color={KIND_COLOR[entry.kind]} style={{ marginInlineEnd: 8 }}>
                    {KIND_LABEL[entry.kind]}
                  </Tag>
                  <Typography.Text strong>{entry.title}</Typography.Text>
                </div>
                <Typography.Paragraph style={{ margin: 0, color: '#52525b' }}>
                  {entry.text}
                </Typography.Paragraph>
              </Card>
            ))}
          </Space>
        </div>
      ))}
    </>
  )
}
