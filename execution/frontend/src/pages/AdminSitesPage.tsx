import { useEffect, useState } from 'react'
import {
  Button, Card, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Table, Tag,
  Typography, Upload, message,
} from 'antd'
import { PlusOutlined, UploadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import {
  SiteFull, createSite, deleteSite, getAdminSites, syncSite, updateSite, uploadWatermark,
} from '../api'

export default function AdminSitesPage() {
  const [sites, setSites] = useState<SiteFull[]>([])
  const [editing, setEditing] = useState<SiteFull | null>(null)
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()

  const load = () => getAdminSites().then(setSites)
  useEffect(() => { load() }, [])

  const openForm = (site: SiteFull | null) => {
    setEditing(site)
    setOpen(true)
  }

  // Найдено при ручной проверке: с destroyOnHidden Modal размонтирует <Form>,
  // пока закрыт, и form.resetFields()/setFieldsValue() вызванные синхронно
  // в openForm() (до применения setOpen) попадали на ещё не подключенный
  // к DOM экземпляр формы — antd молча принимал значения, но ругался в
  // консоль ("Instance created by useForm is not connected to any Form
  // element"). Тот же класс проблемы, что и с unhandled rejection в
  // предыдущих задачах: функционально работает, но засоряет консоль.
  // Переносим установку значений в эффект по open — на этот момент Modal
  // уже примонтировал форму в этом же цикле рендера.
  useEffect(() => {
    if (!open) return
    form.resetFields()
    // Токен приходит маской — подставлять её в поле нельзя, иначе маска
    // уедет обратно на сервер как новое значение.
    form.setFieldsValue(editing ? { ...editing, api_token: '' } : {
      publish_target: 'pages', cover_mode: 'prompt', is_active: true,
    })
  }, [open])

  const submit = async (values: Partial<SiteFull>) => {
    try {
      if (editing) await updateSite(editing.id, values)
      else await createSite(values)
      setOpen(false)
      load()
    } catch {
      // Находка Task 21-23 (LoginPage/ArticlesPage/BatchPage) — тот же класс
      // проблемы: antd Form не ждёт и не перехватывает промис из onFinish,
      // без catch отказ createSite/updateSite (например, дублирующийся домен
      // или случайный 500) стал бы unhandled rejection в консоли браузера.
      // Сообщение об ошибке уже показывает интерцептор api.ts — здесь catch
      // нужен только чтобы погасить промис; модалка остаётся открытой
      // с заполненной формой, что само по себе разумное поведение при ошибке.
    }
  }

  const sync = async (site: SiteFull) => {
    const result = await syncSite(site.id)
    if (result.ok) {
      message.success(`Раздел ${result.url_prefix}, статей в нём ${result.pages}, `
                      + `картинок в эталоне ${result.reference_images}`)
      load()
    } else {
      // Ошибка чужого сайта показывается целиком: администратору нужно понять,
      // что именно не так — токен, id раздела или id эталона.
      message.error(result.detail, 8)
    }
    // sync_site (app/api/admin_sites.py) сознательно всегда отвечает 200
    // с {ok, detail} для всех диагностируемых отказов чужого сайта (плохой
    // токен, не тот id эталона, недоступный раздел и т.п.) — именно чтобы
    // сюда пришёл предсказуемый результат, а не исключение. syncSite() может
    // отклониться только при по-настоящему неожиданном сбое (сеть, 500),
    // который уже покрыт глобальным интерцептором api.ts — отдельный
    // try/catch здесь не нужен.
  }

  return (
    <>
      <Space style={{ marginBottom: 16, justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Сайты</Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => openForm(null)}>
          Добавить сайт
        </Button>
      </Space>

      <Card styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="id" dataSource={sites} pagination={false}
          columns={[
            { title: 'Название', dataIndex: 'name' },
            { title: 'Домен', dataIndex: 'domain' },
            { title: 'Токен', dataIndex: 'api_token', width: 140 },
            {
              title: 'Раздел', width: 180,
              render: (_, r: SiteFull) => r.articles_url_prefix
                ? `${r.articles_url_prefix} (parent ${r.articles_parent_id ?? '—'})`
                : <Tag color="warning">не синхронизирован</Tag>,
            },
            {
              title: 'Эталон', width: 190,
              render: (_, r: SiteFull) => r.reference_synced_at
                ? `${r.reference_images} карт. · ${dayjs(r.reference_synced_at).format('DD.MM HH:mm')}`
                : '—',
            },
            {
              title: 'Знак', width: 90,
              render: (_, r: SiteFull) => r.watermark_path
                ? <Tag color="success">есть</Tag> : <Tag>нет</Tag>,
            },
            {
              title: '', width: 320,
              render: (_, r: SiteFull) => (
                <Space>
                  <Button size="small" onClick={() => sync(r)}>
                    Проверить и синхронизировать
                  </Button>
                  <Upload
                    showUploadList={false}
                    beforeUpload={async file => {
                      try {
                        await uploadWatermark(r.id, file as File)
                        message.success('Водяной знак загружен')
                        load()
                      } catch {
                        // Тот же класс проблемы, что и в submit() выше: Upload
                        // тоже не ждёт и не обрабатывает отклонённый промис
                        // beforeUpload — без catch отказ загрузки (например,
                        // файл не картинка и бэкенд вернул 4xx) стал бы
                        // unhandled rejection, хотя текст ошибки уже показан
                        // интерцептором api.ts.
                      }
                      return false
                    }}
                  >
                    <Button size="small" icon={<UploadOutlined />}>Знак</Button>
                  </Upload>
                  <Button size="small" type="link" onClick={() => openForm(r)}>Правка</Button>
                  {/* Обязательная находка: было мгновенное необратимое удаление
                      по одному клику на маленькой ссылке в плотной строке
                      таблицы — delete_site (app/api/admin_sites.py) это
                      жёсткий db.delete без мягкого удаления, восстановить
                      карточку можно только руками в БД или заведением заново
                      (включая повторный ввод токена и повторную синхронизацию
                      эталона). Для сравнения — в BatchPage.tsx (Task 23) кнопка
                      «Повторить генерацию» одной статьи, действие несравнимо
                      менее затратное, уже защищена Popconfirm; здесь для более
                      разрушительного действия защиты не было вовсе. */}
                  <Popconfirm
                    title="Удалить сайт?"
                    description={<>
                      Токен, тематика, таксономия и путь к водяному знаку
                      будут удалены безвозвратно. Чтобы вернуть сайт, придётся
                      завести карточку заново, ввести токен и синхронизировать
                      эталон с разделом.
                    </>}
                    okText="Удалить" okType="danger" cancelText="Отмена"
                    onConfirm={async () => { await deleteSite(r.id); load() }}
                  >
                    <Button size="small" type="link" danger>Удалить</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal open={open} onCancel={() => setOpen(false)} onOk={form.submit} width={720}
             title={editing ? `Сайт ${editing.domain}` : 'Новый сайт'} destroyOnHidden>
        <Form form={form} layout="vertical" onFinish={submit} requiredMark={false}>
          <Form.Item name="name" label="Название" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="domain" label="Домен" rules={[{ required: true }]}>
            <Input placeholder="stroybaza-samara.ru" />
          </Form.Item>
          <Form.Item name="base_url" label="Базовый URL" rules={[{ required: true }]}>
            <Input placeholder="https://stroybaza-samara.ru" />
          </Form.Item>
          <Form.Item name="api_token" label="Токен API"
                     extra={editing ? 'Пусто — оставить текущий токен' : undefined}
                     rules={[{ required: !editing, message: 'Токен обязателен' }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="site_description" label="О чём сайт и для кого"
                     extra="Тематика и аудитория — по этому описанию подбираются темы
                            статей. Чем конкретнее, тем меньше промахов."
                     rules={[{ required: true, message: 'Без описания темы будут мимо' }]}>
            <Input.TextArea rows={3}
                            placeholder="Строительная база в Самаре: материалы для
                                         частных застройщиков, аудитория — люди,
                                         строящие дом своими силами или с подрядчиком" />
          </Form.Item>
          <Form.Item name="tone_of_voice" label="Тон материалов">
            <Input.TextArea rows={2}
                            placeholder="практичный, без рекламных обещаний,
                                         обращение на «вы»" />
          </Form.Item>
          <Form.Item name="publish_target" label="Куда публиковать">
            <Select options={[
              { value: 'pages', label: 'Страницы (staticpages)' },
              { value: 'articles', label: 'Раздел articles' },
            ]} />
          </Form.Item>
          {/* Префикс url не вводится: он берётся с самой родительской страницы
              при синхронизации, иначе рано или поздно разъедется с сайтом. */}
          <Form.Item name="articles_parent_id" label="ID родительской страницы раздела"
                     extra={editing?.articles_url_prefix
                       ? `Раздел на сайте: ${editing.articles_url_prefix}`
                       : 'Раздел определится при синхронизации'}
                     rules={[{ required: true, message: 'Без раздела публиковать некуда' }]}>
            <InputNumber style={{ width: '100%' }} placeholder="25" />
          </Form.Item>
          <Form.Item name="reference_article_id" label="ID эталонной статьи"
                     extra={editing?.reference_synced_at
                       ? `Синхронизирована ${dayjs(editing.reference_synced_at)
                            .format('DD.MM.YYYY HH:mm')}, картинок в ней:
                            ${editing.reference_images}`
                       : `Её разметка — шаблон для всех статей сайта, а число картинок
                          в ней задаёт число картинок в новых статьях`}
                     rules={[{ required: true, message: 'Эталон обязателен' }]}>
            <InputNumber style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="image_style_prompt" label="Стиль контентных картинок">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="cover_mode" label="Обложка">
            <Select options={[
              { value: 'prompt', label: 'По своему промпту' },
              { value: 'like_existing', label: 'Как существующие обложки сайта' },
            ]} />
          </Form.Item>
          <Form.Item name="cover_style_prompt" label="Стиль обложки">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Typography.Text type="secondary">
            Ниже — раздел карточек строителей (план 2), к обложкам статей
            отношения не имеет.
          </Typography.Text>
          <Form.Item name="builder_parent_id" label="ID родительской страницы для карточек строителей"
                     style={{ marginTop: 12 }}
                     extra="Страницы компаний из партий строителей создаются как дочерние
                            для этой страницы. Пока не заполнено — создание страниц компании
                            этого сайта завершится ошибкой.">
            <InputNumber style={{ width: '100%' }} placeholder="25" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
