import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  Alert, Button, Card, Input, Popconfirm, Space, Table, Tag, Typography, message,
} from 'antd'
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { ArticleRow, Batch, getBatch, retryArticle, runBatch, saveTopics } from '../api'

const EDITABLE = ['topics_pending', 'topics_review', 'failed']

const ARTICLE_STATUS: Record<string, { color: string; label: string }> = {
  draft: { color: 'default', label: 'Ожидает' },
  generating: { color: 'processing', label: 'Генерируется' },
  generated: { color: 'processing', label: 'Собрана' },
  published: { color: 'success', label: 'Черновик на сайте' },
  failed: { color: 'error', label: 'Ошибка' },
}

export default function BatchPage() {
  const { id } = useParams()
  const batchId = Number(id)
  const [batch, setBatch] = useState<Batch | null>(null)
  const [topics, setTopics] = useState<string[]>([])
  // Находка №1 ревью Task 23: изначально было отдельное состояние `saving`,
  // которое включалось только внутри persist() и не покрывало последующий
  // runBatch(). Если темы не менялись, persist() вообще не вызывался —
  // кнопка «Запустить генерацию» ни разу не становилась занятой, и второй
  // клик до ответа runBatch() отправлял бы второй запрос ещё до того, как
  // первый успел перевести batch.status в "running" на бэкенде
  // (app/api/article_batches.py, run()). Бэкенд эту гонку сужает (синхронный
  // перевод в "running" до постановки в очередь, см. комментарий там же), но
  // не отменяет полностью — окно между кликом и ответом сервера всё ещё
  // открыто, особенно на медленной сети, когда пользователь скорее всего
  // решит, что первый клик не сработал, и кликнет снова. `starting`
  // накрывает ВЕСЬ start() целиком — persist, runBatch и финальный load(), —
  // поэтому отдельное `saving` для одного лишь persist() было бы избыточным
  // состоянием, которое никто не читает: persist() вызывается только отсюда.
  const [starting, setStarting] = useState(false)

  const load = () => getBatch(batchId).then(b => {
    setBatch(b)
    // Находка №2 ревью Task 23 (см. также комментарий у `editable` ниже):
    // темы в форму подставляем только когда партия действительно
    // редактируема — то есть ещё и нет ни одной опубликованной статьи.
    // Иначе бессмысленно готовить состояние формы, которая не будет
    // показана.
    const hasPublished = b.articles.some(a => a.status === 'published')
    if (EDITABLE.includes(b.status) && !hasPublished) setTopics(b.articles.map(a => a.topic))
    return b
  })

  useEffect(() => { load() }, [batchId])

  useEffect(() => {
    if (!batch) return
    const active = batch.status === 'topics_pending' || batch.status === 'running'
      || batch.articles.some(a => a.status === 'generating')
    if (!active) return
    const timer = setInterval(load, 5000)
    return () => clearInterval(timer)
  }, [batch])

  if (!batch) return null

  // Находка №2 ревью Task 23: EDITABLE.includes(batch.status) одного статуса
  // недостаточно. batch.status становится "failed" не только когда подбор
  // тем не удался (тогда статей ещё нет вовсе), но и когда партия честно
  // частично собралась — часть Article уже status="published" с реальными
  // remote_page_id/remote_url на сайте, — а потом сборка оборвалась по
  // таймауту или ошибке конфигурации (app/tasks.py, обработчик
  // SoftTimeLimitExceeded). В этом случае бэкендный save_topics
  // (app/api/article_batches.py) откажет 400 при ЛЮБОЙ попытке сохранить
  // темы — правка тем для партии с опубликованными статьями осмысленно
  // запрещена (Task 18: удалить/переписать статьи, часть из которых уже
  // реально существует на сайте, — потерять журнал публикаций). Не полагаемся
  // на то, что бэкенд откажет: заранее не показываем форму редактирования,
  // которая гарантированно упадёт, а сразу показываем табличный режим с
  // кнопкой повтора для конкретных упавших статей.
  const hasPublished = batch.articles.some(a => a.status === 'published')
  const editable = EDITABLE.includes(batch.status) && !hasPublished

  const persist = async (next: string[]) => {
    setBatch(await saveTopics(batchId, next))
    setTopics(next)
  }

  const start = async () => {
    setStarting(true)
    try {
      // Лёгкий пункт (план Task 23): раньше здесь темы сохранялись только
      // если `topics.join('|') !== batch.articles.map(a => a.topic).join('|')`
      // отличались. Сравнение через склейку с разделителем '|' даёт ложные
      // совпадения: если тема реально содержит символ '|' (например,
      // «AI | будущее контента» — не экзотика для заголовка статьи), два
      // РАЗНЫХ массива тем могут дать одинаковую строку после join('|')
      // (["a|b", "c"] и ["a", "b|c"]), и реально изменённые темы не будут
      // сохранены перед запуском — в производство уйдут старые формулировки.
      // Решение: убрать сравнение вовсе и всегда звать persist(topics) перед
      // runBatch. saveTopics — недорогая операция (обновление одной таблицы
      // статей в рамках одной партии), лишний HTTP-вызов, когда темы и так
      // не менялись, дешевле, чем риск потерять правки менеджера.
      await persist(topics)
      await runBatch(batchId)
      message.success(`Запущено. Черновики появятся на ${batch.site_domain}`)
      await load()
    } finally {
      setStarting(false)
    }
  }

  return (
    <>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        Партия №{batch.id} — {batch.site_name}
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        Черновики создаются на <b>{batch.site_domain}</b>. Публикует их менеджер
        вручную в админке сайта.
      </Typography.Paragraph>

      {batch.error_text && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }}
               message="Не удалось подобрать темы" description={batch.error_text} />
      )}

      {batch.status === 'topics_pending' && (
        <Alert type="info" showIcon style={{ marginBottom: 16 }}
               message="Подбираем темы — обычно занимает до минуты" />
      )}

      {/* Находка №2: явно объясняем, почему редактирование тем недоступно,
          вместо того чтобы пользователь наткнулся на это только через отказ
          бэкенда при попытке сохранить. */}
      {batch.status === 'failed' && hasPublished && (
        <Alert type="warning" showIcon style={{ marginBottom: 16 }}
               message="Часть статей уже опубликована"
               description="Партия прервалась после того, как часть статей была реально
                            опубликована на сайте. Правка списка тем для такой партии
                            недоступна — повтори генерацию для конкретной упавшей статьи
                            в таблице ниже." />
      )}

      {editable ? (
        <Card title="Согласование тем" extra={
          <Space>
            <Button icon={<PlusOutlined />} disabled={starting}
                    onClick={() => setTopics([...topics, ''])}>
              Добавить тему
            </Button>
            {/* Находка №1: loading и disabled завязаны на `starting`, который
                выставлен на всё время start() — сохранение тем (если нужно),
                запуск партии и финальную перезагрузку данных. Кнопка недоступна
                для повторного клика весь этот период, а не только на время
                persist(). */}
            <Button type="primary" loading={starting}
                    disabled={starting || topics.filter(t => t.trim()).length === 0}
                    onClick={start}>
              Запустить генерацию
            </Button>
          </Space>
        }>
          <Space direction="vertical" style={{ width: '100%' }}>
            {topics.map((topic, index) => (
              <Space.Compact key={index} style={{ width: '100%' }}>
                <Input
                  value={topic}
                  placeholder="Заголовок статьи"
                  disabled={starting}
                  onChange={e => {
                    const next = [...topics]
                    next[index] = e.target.value
                    setTopics(next)
                  }}
                />
                <Button icon={<DeleteOutlined />} disabled={starting}
                        onClick={() => setTopics(topics.filter((_, i) => i !== index))} />
              </Space.Compact>
            ))}
            {topics.length === 0 && (
              <Typography.Text type="secondary">
                Тем нет — добавь свои или создай партию заново.
              </Typography.Text>
            )}
          </Space>
        </Card>
      ) : (
        <Card styles={{ body: { padding: 0 } }}>
          <Table
            rowKey="id"
            dataSource={batch.articles}
            pagination={false}
            columns={[
              { title: 'Тема', dataIndex: 'topic' },
              {
                title: 'Статус', dataIndex: 'status', width: 200,
                render: (s: string) => (
                  <Tag color={ARTICLE_STATUS[s]?.color}>{ARTICLE_STATUS[s]?.label ?? s}</Tag>
                ),
              },
              {
                title: 'Черновик', width: 140,
                render: (_, r: ArticleRow) => r.remote_url
                  ? <a href={r.remote_url} target="_blank" rel="noreferrer">открыть</a>
                  : '—',
              },
              {
                title: '', width: 60,
                render: (_, r: ArticleRow) => r.status === 'failed' ? (
                  <Popconfirm title="Повторить генерацию этой статьи?"
                              onConfirm={async () => { await retryArticle(r.id); load() }}>
                    <Button type="text" icon={<ReloadOutlined />} />
                  </Popconfirm>
                ) : null,
              },
            ]}
            expandable={{
              expandedRowRender: (r: ArticleRow) => (
                <Typography.Text type="danger">{r.error_text}</Typography.Text>
              ),
              rowExpandable: (r: ArticleRow) => Boolean(r.error_text),
            }}
          />
        </Card>
      )}
    </>
  )
}
