import { useEffect, useState } from 'react'
import dayjs from 'dayjs'
import {
  Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message,
} from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined, SyncOutlined } from '@ant-design/icons'
import {
  MetaProject, MetaProjectIn, SiteBrief, WordstatRegion, createMetaProject, deleteMetaProject,
  getMetaProjects, getSites, runMetaProject, searchWordstatRegions, updateMetaProject,
} from '../api'
import HelpButton from '../help'
import CategoryMetaTree from './CategoryMetaTree'

const isRunning = (p: MetaProject) => Boolean(p.run && !p.run.stale)

function ProjectState({ project }: { project: MetaProject }) {
  const { run } = project
  if (run && run.stale) {
    return <Tag color="error">Обновление оборвалось — запустите заново</Tag>
  }
  if (run) {
    const waiting = run.wait_until && dayjs(run.wait_until).isAfter(dayjs())
    return (
      <Space direction="vertical" size={2}>
        <Tag color="processing">
          {run.total ? `В работе: ${run.done + run.failed} из ${run.total}` : 'Готовим список категорий'}
        </Tag>
        {waiting && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            ждём квоту Wordstat, продолжим ~в {dayjs(run.wait_until).format('HH:mm')}
          </Typography.Text>
        )}
      </Space>
    )
  }
  return (
    <Space direction="vertical" size={2}>
      <Typography.Text>
        {project.updated_at
          ? `Теги обновлены ${dayjs(project.updated_at).format('DD.MM.YYYY HH:mm')}`
          : 'Ещё не обновлялись'}
      </Typography.Text>
      {project.last_error && (
        <Typography.Text type="danger" style={{ fontSize: 12 }}>
          Последний запуск: {project.last_error}
        </Typography.Text>
      )}
    </Space>
  )
}

function ProjectModal({ project, sites, usedSiteIds, onClose, onSaved }: {
  project: MetaProject | null
  sites: SiteBrief[]
  usedSiteIds: number[]
  onClose: () => void
  onSaved: () => void
}) {
  const [form] = Form.useForm<MetaProjectIn>()
  const [regions, setRegions] = useState<WordstatRegion[]>([])
  const [searching, setSearching] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (project) {
      form.setFieldsValue({
        site_id: project.site_id, city: project.city, city_in: project.city_in,
        brand: project.brand, wordstat_region_id: project.wordstat_region_id ?? undefined,
      })
      if (project.wordstat_region_id) {
        setRegions([{ id: project.wordstat_region_id, label: project.city,
                      path: `регион ${project.wordstat_region_id}` }])
      }
    }
  }, [project])

  const findRegions = async () => {
    const city = String(form.getFieldValue('city') || '').trim()
    if (!city) { message.warning('Сначала укажите город'); return }
    setSearching(true)
    try {
      const found = await searchWordstatRegions(city)
      setRegions(found)
      const exact = found.filter(r => r.label.toLowerCase() === city.toLowerCase())
      if (exact.length === 1) form.setFieldValue('wordstat_region_id', exact[0].id)
      if (!found.length) message.warning('Такого города в регионах Wordstat нет')
    } catch { /* сообщение уже показал интерцептор */ }
    finally { setSearching(false) }
  }

  const submit = async (values: MetaProjectIn) => {
    setSaving(true)
    try {
      const payload = { ...values, city_in: values.city_in || '' }
      if (project) await updateMetaProject(project.site_id, payload)
      else await createMetaProject(payload)
      message.success('Проект сохранён')
      onSaved()
    } catch { /* сообщение уже показал интерцептор */ }
    finally { setSaving(false) }
  }

  const siteOptions = sites
    .filter(s => project ? s.id === project.site_id : !usedSiteIds.includes(s.id))
    .map(s => ({ value: s.id, label: `${s.name} (${s.domain})` }))

  return (
    <Modal open title={project ? `Проект — ${project.name}` : 'Новый проект'} okText="Сохранить"
           cancelText="Отмена" confirmLoading={saving} onCancel={onClose}
           onOk={() => form.submit()} destroyOnClose>
      <Form form={form} layout="vertical" onFinish={submit} requiredMark={false}>
        <Form.Item name="site_id" label="Сайт" rules={[{ required: true, message: 'Выберите сайт' }]}>
          <Select disabled={Boolean(project)} options={siteOptions} placeholder="Сайт из раздела «Сайты»" />
        </Form.Item>
        <Form.Item label="Город" required>
          <Space.Compact style={{ width: '100%' }}>
            <Form.Item name="city" noStyle rules={[{ required: true, message: 'Укажите город' }]}>
              <Input placeholder="Москва" />
            </Form.Item>
            <Button onClick={findRegions} loading={searching}>Найти регион</Button>
          </Space.Compact>
        </Form.Item>
        <Form.Item name="wordstat_region_id" label="Регион Wordstat"
                   rules={[{ required: true, message: 'Найдите и выберите регион' }]}
                   extra="Статистика запросов берётся по этому региону">
          <Select placeholder="Нажмите «Найти регион»"
                  options={regions.map(r => ({ value: r.id, label: `${r.label} — ${r.path}` }))} />
        </Form.Item>
        <Form.Item name="city_in" label="Город с предлогом"
                   extra="«в Москве», «во Владимире». Оставьте пустым — определится автоматически">
          <Input placeholder="в Москве" />
        </Form.Item>
        <Form.Item name="brand" label="Бренд в конце title"
                   rules={[{ required: true, message: 'Укажите бренд' }]}>
          <Input placeholder="Стройбаза" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

export default function CategoryMetaPage() {
  const [projects, setProjects] = useState<MetaProject[]>([])
  const [sites, setSites] = useState<SiteBrief[]>([])
  const [editing, setEditing] = useState<MetaProject | 'new' | null>(null)
  const [busySite, setBusySite] = useState<number | null>(null)

  const load = () => getMetaProjects().then(setProjects)

  useEffect(() => { load(); getSites().then(setSites) }, [])

  useEffect(() => {
    if (!projects.some(isRunning)) return
    const timer = setInterval(load, 10000)
    return () => clearInterval(timer)
  }, [projects])

  const run = async (siteId: number) => {
    setBusySite(siteId)
    try {
      await runMetaProject(siteId)
      message.success('Обновление запущено — идёт в фоне')
      await load()
    } catch { /* сообщение уже показал интерцептор */ }
    finally { setBusySite(null) }
  }

  const remove = async (siteId: number) => {
    try { await deleteMetaProject(siteId); await load() }
    catch { /* сообщение уже показал интерцептор */ }
  }

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }} wrap>
        <Typography.Title level={4} style={{ margin: 0 }}>Метатеги категорий</Typography.Title>
        <Space>
          <HelpButton section="categoryMeta" />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditing('new')}>
            Добавить проект
          </Button>
        </Space>
      </Space>

      <Card styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="site_id"
          dataSource={projects}
          pagination={false}
          scroll={{ x: 720 }}
          locale={{ emptyText: 'Проектов пока нет — добавьте сайт' }}
          columns={[
            {
              title: 'Сайт',
              render: (_, p: MetaProject) => (
                <Space direction="vertical" size={0}>
                  <Typography.Text strong>{p.name}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>{p.domain}</Typography.Text>
                  {p.error_count > 0 && (
                    <Tag color="error" style={{ marginTop: 4 }}>
                      Категорий с ошибками: {p.error_count}
                    </Tag>
                  )}
                </Space>
              ),
            },
            { title: 'Город', dataIndex: 'city', width: 140 },
            { title: 'Состояние', width: 280, render: (_, p: MetaProject) => <ProjectState project={p} /> },
            {
              title: '', width: 260,
              render: (_, p: MetaProject) => (
                <Space>
                  <Popconfirm
                    title="Обновить метатеги всех категорий?"
                    description="Теги перезапишутся на сайте, в том числе заполненные вручную."
                    onConfirm={() => run(p.site_id)} disabled={isRunning(p)}>
                    <Button icon={<SyncOutlined />} loading={busySite === p.site_id}
                            disabled={isRunning(p)}>
                      Обновить метатеги
                    </Button>
                  </Popconfirm>
                  <Button type="text" icon={<EditOutlined />} onClick={() => setEditing(p)} />
                  <Popconfirm title="Убрать проект из раздела? Теги на сайте останутся."
                              onConfirm={() => remove(p.site_id)}>
                    <Button type="text" icon={<DeleteOutlined />} disabled={isRunning(p)} />
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
          expandable={{
            expandedRowRender: (p: MetaProject) => (
              <CategoryMetaTree siteId={p.site_id} active={isRunning(p)} />
            ),
          }}
        />
      </Card>

      {editing && (
        <ProjectModal
          project={editing === 'new' ? null : editing}
          sites={sites}
          usedSiteIds={projects.map(p => p.site_id)}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load() }}
        />
      )}
    </>
  )
}
