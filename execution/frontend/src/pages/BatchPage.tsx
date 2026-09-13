import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  Alert, Button, Card, Input, Popconfirm, Space, Table, Tag, Typography, message,
} from 'antd'
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import {
  ArticleRow, Batch, getBatch, regenerateArticle, retryArticle, runBatch, saveTopics,
} from '../api'
import { BATCH_STATUS, RUNTIME_STATE } from '../statuses'

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
    // Партия, признанная зависшей, сама уже не изменится: собиравшая её задача
    // мертва, и статусы в БД некому исправить. До 2026-09-13 поллинг этого не
    // знал и крутился вечно — страница партии 25 опрашивала сервер каждые пять
    // секунд девять дней подряд, показывая всё то же самое.
    if (batch.runtime_state === 'stuck') return
    const active = batch.status === 'topics_pending' || batch.status === 'running'
      || batch.articles.some(a => a.status === 'generating' || a.regenerating)
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

  const publishedCount = batch.articles.filter(a => a.status === 'published').length
  const unfinished = batch.articles.length - publishedCount
  const isStuck = batch.runtime_state === 'stuck'
  // Сборка реально идёт (или вот-вот начнётся) — в это время нельзя предлагать
  // ни «Дособрать партию», ни повтор отдельной статьи: задача сама дойдёт до
  // каждой неопубликованной статьи, а параллельный запуск собрал бы её второй
  // раз и второй раз за неё заплатил.
  const busy = batch.runtime_state === 'working' || batch.runtime_state === 'queued'
  // Зависшую партию перезапускает тот же эндпоинт run(): он сам приводит в
  // порядок её состояние, а сборка пропускает уже опубликованные статьи.
  const canContinue = unfinished > 0 && !busy
    && (isStuck || batch.status === 'done' || batch.status === 'failed')
  const stateTag = batch.runtime_state
    ? RUNTIME_STATE[batch.runtime_state]
    : BATCH_STATUS[batch.status]

  const continueBatch = async () => {
    setStarting(true)
    try {
      await runBatch(batchId)
      message.success('Сборка продолжена — опубликованные статьи пропускаются')
      await load()
    } finally {
      setStarting(false)
    }
  }

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

      {/* Статуса партии на этой странице не было вовсе — он показывался только
          в списке партий. Из-за этого оборвавшаяся партия 25 выглядела так же,
          как работающая: таблица со статьями и никаких признаков того, идёт
          сборка или давно умерла. Тег берём из runtime_state, когда он есть:
          «Генерируется» в статусе партии означает лишь, что кто-то нажал
          кнопку, — статус выставляет эндпоинт до постановки задачи в очередь. */}
      <Space style={{ marginBottom: 16 }} size={12}>
        <Tag color={stateTag?.color}>{stateTag?.label ?? batch.status}</Tag>
        {batch.articles.length > 0 && (
          <Typography.Text type="secondary">
            готово {publishedCount} из {batch.articles.length}
          </Typography.Text>
        )}
      </Space>

      {batch.error_text && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }}
               message={batch.articles.length === 0
                 ? 'Не удалось подобрать темы'
                 : 'Сборка прервалась'}
               description={batch.error_text} />
      )}

      {isStuck && (
        <Alert type="warning" showIcon style={{ marginBottom: 16 }}
               message="Похоже, задача зависла"
               description={
                 <>
                   Партия числится в работе
                   {batch.run_requested_at
                     ? ` с ${dayjs(batch.run_requested_at).format('D MMMM, HH:mm')}`
                     : ''}
                   , но собирающая её задача не подаёт признаков жизни — скорее
                   всего, она оборвалась. Нажми «Дособрать партию»: уже
                   опубликованные статьи пропустятся, платить за них второй раз
                   не придётся.
                 </>
               }
               action={
                 <Button type="primary" loading={starting} onClick={continueBatch}>
                   Дособрать партию
                 </Button>
               } />
      )}

      {batch.runtime_state === 'queued' && (
        <Alert type="info" showIcon style={{ marginBottom: 16 }}
               message="Ждёт свободного места"
               description="Одновременно собираются не больше двух партий. Эта стоит
                            в очереди и начнётся, как только освободится место." />
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
        <Card
          styles={{ body: { padding: 0 } }}
          title="Статьи"
          extra={canContinue && !isStuck && (
            // При isStuck кнопка уже стоит в предупреждении выше — второй раз
            // рядом с таблицей она была бы шумом.
            <Popconfirm
              title={`Дособрать ${unfinished} ${unfinished === 1 ? 'статью' : 'статьи'}?`}
              description="Опубликованные статьи пропускаются — заново за них не платим."
              onConfirm={continueBatch}>
              <Button type="primary" loading={starting}>Дособрать партию</Button>
            </Popconfirm>
          )}
        >
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
                title: '', width: 300,
                render: (_, r: ArticleRow) => {
                  // draft здесь появился 2026-09-13. Бэкенд повтор статьи в
                  // этом статусе принимал и раньше (отклоняются только
                  // published и generating), но кнопки не было — у партии 25
                  // 23 статьи висели в «Ожидает» без единого действия рядом.
                  // Пока сборка идёт, кнопку не показываем: задача сама дойдёт
                  // до этой статьи, а параллельный повтор собрал бы её дважды.
                  if (r.status === 'failed' || (r.status === 'draft' && !busy)) {
                    return (
                      <Popconfirm title={r.status === 'draft'
                        ? 'Собрать эту статью отдельно?'
                        : 'Повторить генерацию этой статьи?'}
                                  onConfirm={async () => { await retryArticle(r.id); load() }}>
                        <Button type="text" icon={<ReloadOutlined />} />
                      </Popconfirm>
                    )
                  }
                  if (r.status === 'published') {
                    const regen = async (parts: { images?: boolean; cover?: boolean }) => {
                      await regenerateArticle(r.id, parts)
                      load()
                    }
                    return (
                      <Space direction="vertical" size={2}>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          Перегенерировать картинки
                        </Typography.Text>
                        <Space size={4}>
                          <Popconfirm title="Перегенерировать картинки внутри текста статьи?"
                                      onConfirm={() => regen({ images: true })}>
                            <Button size="small" loading={r.regenerating}
                                    disabled={r.regenerating}>
                              Только внутри
                            </Button>
                          </Popconfirm>
                          <Popconfirm title="Перегенерировать обложку статьи?"
                                      onConfirm={() => regen({ cover: true })}>
                            <Button size="small" loading={r.regenerating}
                                    disabled={r.regenerating}>
                              Обложку
                            </Button>
                          </Popconfirm>
                          <Popconfirm title="Перегенерировать все картинки статьи (внутри и обложку)?"
                                      onConfirm={() => regen({ images: true, cover: true })}>
                            <Button size="small" loading={r.regenerating}
                                    disabled={r.regenerating}>
                              Все
                            </Button>
                          </Popconfirm>
                        </Space>
                      </Space>
                    )
                  }
                  return null
                },
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
