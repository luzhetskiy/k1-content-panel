import { useEffect, useState } from 'react'
import dayjs from 'dayjs'
import {
  Button, Card, Dropdown, Form, InputNumber, Modal, Popconfirm, Select, Space, Table, Tag,
  Typography, message,
} from 'antd'
import { DeleteOutlined, DownOutlined, EditOutlined, PlusOutlined, SyncOutlined } from '@ant-design/icons'
import {
  HouseProject, HouseProjectIn, RewriteMode, SiteBrief, createHouseProject, deleteHouseProject,
  getHouseProjects, getSites, runHouseProject, updateHouseProject,
} from '../api'
import HelpButton from '../help'
import HouseProjectPages from './HouseProjectPages'

// Оборвавшийся запуск проект не держит — «Запустить рерайт» снова доступна.
const isBusy = (p: HouseProject) => p.busy

function ProjectState({ project }: { project: HouseProject }) {
  const { run } = project
  if (run && run.stale) {
    return <Tag color="error">Рерайт оборвался — запустите заново</Tag>
  }
  if (run) {
    return (
      <Tag color="processing">
        {run.total ? `В работе: ${run.done + run.failed} из ${run.total}` : 'Читаем страницы с сайта'}
      </Tag>
    )
  }
  if (project.busy) return <Tag color="processing">Переписывается страница</Tag>
  return (
    <Space direction="vertical" size={2}>
      <Typography.Text>
        {project.pages ? `Переписано ${project.rewritten} из ${project.pages}` : 'Страницы ещё не читались'}
      </Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {project.last_rewritten_at
          ? `Последний рерайт ${dayjs(project.last_rewritten_at).format('DD.MM.YYYY HH:mm')}`
          : 'Рерайта ещё не было'}
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
  project: HouseProject | null
  sites: SiteBrief[]
  usedSiteIds: number[]
  onClose: () => void
  onSaved: () => void
}) {
  const [form] = Form.useForm<HouseProjectIn>()
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (project) form.setFieldsValue({ site_id: project.site_id, parent_id: project.parent_id })
  }, [project])

  const submit = async (values: HouseProjectIn) => {
    setSaving(true)
    try {
      const saved = project
        ? await updateHouseProject(project.site_id, values)
        : await createHouseProject(values)
      message.success(`Проект сохранён: «${saved.parent_title}»`)
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
        <Form.Item name="parent_id" label="ID родительской страницы"
                   rules={[{ required: true, message: 'Укажите ID страницы' }]}
                   extra="Страница раздела проектов в админке сайта (на stroybaza-samara.ru — 15). Переписываются её прямые подстраницы">
          <InputNumber min={1} style={{ width: '100%' }} placeholder="15" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

export default function HouseProjectsPage() {
  const [projects, setProjects] = useState<HouseProject[]>([])
  const [sites, setSites] = useState<SiteBrief[]>([])
  const [editing, setEditing] = useState<HouseProject | 'new' | null>(null)
  const [busySite, setBusySite] = useState<number | null>(null)

  const load = () => getHouseProjects().then(setProjects)

  useEffect(() => { load(); getSites().then(setSites) }, [])

  useEffect(() => {
    if (!projects.some(isBusy)) return
    const timer = setInterval(load, 10000)
    return () => clearInterval(timer)
  }, [projects])

  const start = async (project: HouseProject, mode: RewriteMode) => {
    setBusySite(project.site_id)
    try {
      await runHouseProject(project.site_id, mode)
      message.success('Рерайт запущен — идёт в фоне')
      await load()
    } catch { /* сообщение уже показал интерцептор */ }
    finally { setBusySite(null) }
  }

  const confirmRun = (project: HouseProject, mode: RewriteMode) => {
    Modal.confirm({
      title: mode === 'new' ? 'Переписать новые страницы?' : 'Переписать все страницы заново?',
      content: mode === 'new'
        ? 'Возьмутся страницы, которые ещё не переписывались, и страницы с ошибкой. Новый текст сразу запишется на сайт.'
        : 'Все страницы раздела перепишутся заново от оригинала. Новый текст сразу запишется на сайт.',
      okText: 'Запустить', cancelText: 'Отмена',
      onOk: () => start(project, mode),
    })
  }

  const remove = async (siteId: number) => {
    try { await deleteHouseProject(siteId); await load() }
    catch { /* сообщение уже показал интерцептор */ }
  }

  // Пока страницы не читались, числа неизвестны: список прочитается при запуске.
  const modeLabel = (p: HouseProject, mode: RewriteMode) => {
    const label = mode === 'new' ? 'Только новые' : 'Все заново'
    if (!p.pages) return label
    return `${label} (${mode === 'new' ? p.new_count : p.pages})`
  }

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }} wrap>
        <Typography.Title level={4} style={{ margin: 0 }}>Проекты домов</Typography.Title>
        <Space>
          <HelpButton section="houseProjects" />
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
              render: (_, p: HouseProject) => (
                <Space direction="vertical" size={0}>
                  <Typography.Text strong>{p.name}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>{p.domain}</Typography.Text>
                  {p.error_count > 0 && (
                    <Tag color="error" style={{ marginTop: 4 }}>Страниц с ошибкой: {p.error_count}</Tag>
                  )}
                </Space>
              ),
            },
            {
              title: 'Раздел', width: 200,
              render: (_, p: HouseProject) => `${p.parent_title} (id ${p.parent_id})`,
            },
            { title: 'Состояние', width: 260, render: (_, p: HouseProject) => <ProjectState project={p} /> },
            {
              title: '', width: 260,
              render: (_, p: HouseProject) => (
                <Space>
                  <Dropdown disabled={isBusy(p)} trigger={['click']} menu={{
                    items: [
                      { key: 'new', label: modeLabel(p, 'new') },
                      { key: 'all', label: modeLabel(p, 'all') },
                    ],
                    onClick: ({ key }) => confirmRun(p, key as RewriteMode),
                  }}>
                    <Button icon={<SyncOutlined />} loading={busySite === p.site_id} disabled={isBusy(p)}>
                      Запустить рерайт <DownOutlined />
                    </Button>
                  </Dropdown>
                  <Button type="text" icon={<EditOutlined />} disabled={isBusy(p)}
                          onClick={() => setEditing(p)} />
                  <Popconfirm title="Убрать проект из раздела? Тексты на сайте останутся."
                              onConfirm={() => remove(p.site_id)}>
                    <Button type="text" icon={<DeleteOutlined />} disabled={isBusy(p)} />
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
          expandable={{
            expandedRowRender: (p: HouseProject) => (
              <HouseProjectPages siteId={p.site_id} busy={isBusy(p)} onChanged={load} />
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
