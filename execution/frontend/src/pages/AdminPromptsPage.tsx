import { useEffect, useState } from 'react'
import {
  Button, Card, Input, Select, Space, Tabs, Typography, message,
} from 'antd'
import { Prompt, SiteBrief, getPrompts, getSites, savePrompt, testPrompt } from '../api'

const KEYS = [
  { key: 'topics', label: 'Темы', vars: { count: 5, site_name: 'Стройбаза', site_description: 'Строительная база в Самаре, аудитория — частные застройщики', tone_of_voice: 'практичный, без рекламных обещаний', existing_titles: ['Чем утеплить дом'] } },
  { key: 'article_body', label: 'Текст статьи', vars: { topic: 'Чем утеплить каркасный дом', site_name: 'Стройбаза', site_description: 'Строительная база в Самаре, аудитория — частные застройщики', tone_of_voice: 'практичный, без рекламных обещаний', reference_html: '<article><p>образец</p><img></article>', image_count: 2, image_paths: ['/media/uploads/article-img/article_1-1.webp', '/media/uploads/article-img/article_1-2.webp'] } },
  { key: 'cover', label: 'Обложка', vars: { topic: 'Чем утеплить каркасный дом', cover_style: 'широкая обложка' } },
  { key: 'content_image', label: 'Картинка в тексте', vars: { topic: 'Чем утеплить каркасный дом', paragraph: 'иллюстрация 1 из 2', image_style: 'фото стройки' } },
]

export default function AdminPromptsPage() {
  const [prompts, setPrompts] = useState<Prompt[]>([])
  const [sites, setSites] = useState<SiteBrief[]>([])
  const [siteId, setSiteId] = useState<number | null>(null)
  const [texts, setTexts] = useState<Record<string, string>>({})
  const [result, setResult] = useState<{ rendered: string; answer: string; tokens_total: number; cost: number } | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => getPrompts().then(rows => {
    setPrompts(rows)
    const next: Record<string, string> = {}
    for (const item of KEYS) {
      const override = rows.find(r => r.key === item.key && r.site_id === siteId)
      const global = rows.find(r => r.key === item.key && r.site_id === null)
      // Обязательная находка №1: resolve_prompt (app/ai/prompts.py) игнорирует
      // оверрайд сайта, если тот пуст ПОСЛЕ trim (`override.text.strip()`), и
      // в этом случае реально используемым при генерации текстом становится
      // global.text — это штатный, задокументированный способ «сбросить
      // промпт сайта на глобальный», не удаляя саму запись PromptTemplate.
      // Прежнее `override?.text ?? global?.text ?? ''` этого не учитывало:
      // `??` не срабатывает на существующую пустую строку, только на
      // null/undefined, поэтому админ, очистивший и сохранивший оверрайд,
      // видел бы на экране пустое поле — хотя при следующей генерации статьи
      // реально использовался бы непустой глобальный текст. Расхождение
      // между тем, что видно, и тем, что реально произойдёт. Повторяем здесь
      // ту же проверку .trim(), что и на бэкенде.
      const overrideText = override && override.text.trim() ? override.text : undefined
      next[item.key] = overrideText ?? global?.text ?? ''
    }
    setTexts(next)
  })

  useEffect(() => { getSites().then(setSites) }, [])
  useEffect(() => { load() }, [siteId])

  const save = async (key: string) => {
    try {
      await savePrompt({ key, site_id: siteId, text: texts[key] })
      message.success(siteId ? 'Промпт сохранён для сайта' : 'Глобальный промпт сохранён')
      load()
    } catch {
      // Устоявшийся в проекте паттерн (Task 21-24): onClick не оборачивается
      // antd-формой, которая сама поглощает отказ onFinish, — без catch здесь
      // отклонённый промис savePrompt (например, ошибка шаблона от
      // check_template) стал бы unhandled rejection в консоли, хотя текст
      // ошибки и так уже показан глобальным интерцептором api.ts.
    }
  }

  const runTest = async (key: string) => {
    setBusy(true)
    setResult(null)
    try {
      setResult(await testPrompt(texts[key], KEYS.find(k => k.key === key)!.vars))
    } catch {
      // Тот же класс проблемы, что и в save() выше — testPrompt может
      // отклониться (ошибка шаблона, сбой RouterAI), интерцептор уже покажет
      // сообщение, catch здесь только гасит unhandled rejection.
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Промпты</Typography.Title>
        <Select
          style={{ width: 320 }} value={siteId} onChange={setSiteId}
          options={[{ value: null, label: 'Глобальные значения по умолчанию' },
                    ...sites.map(s => ({ value: s.id, label: `Только для ${s.domain}` }))]}
        />
      </Space>

      <Tabs items={KEYS.map(item => ({
        key: item.key,
        label: item.label,
        children: (
          <Card>
            <Input.TextArea
              rows={16} value={texts[item.key] ?? ''}
              onChange={e => setTexts({ ...texts, [item.key]: e.target.value })}
            />
            <Space style={{ marginTop: 12 }}>
              <Button type="primary" onClick={() => save(item.key)}>Сохранить</Button>
              <Button loading={busy} onClick={() => runTest(item.key)}>Тест</Button>
              <Typography.Text type="secondary">
                Переменные: {Object.keys(item.vars).join(', ')}
              </Typography.Text>
            </Space>

            {result && (
              <div style={{ marginTop: 16 }}>
                <Typography.Title level={5}>Отрендеренный промпт</Typography.Title>
                <pre style={{ background: '#fafafa', padding: 12, borderRadius: 8,
                              whiteSpace: 'pre-wrap' }}>{result.rendered}</pre>
                <Typography.Title level={5}>Ответ модели</Typography.Title>
                <pre style={{ background: '#fafafa', padding: 12, borderRadius: 8,
                              whiteSpace: 'pre-wrap' }}>{result.answer}</pre>
                <Typography.Text type="secondary">
                  Токенов: {result.tokens_total} · стоимость: {result.cost}
                </Typography.Text>
              </div>
            )}
          </Card>
        ),
      }))} />
      {prompts.length === 0 && <Typography.Text type="secondary">Промпты загружаются…</Typography.Text>}
    </>
  )
}
