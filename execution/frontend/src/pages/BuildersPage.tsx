import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Button, Card, Form, InputNumber, Modal, Select, Space, Table, Tag, Typography,
  Upload, message,
} from 'antd'
import { PlusOutlined, UploadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import type { UploadProps } from 'antd'
import {
  CompanyBatchRow, CompanyImportResult, Facets, SiteBrief, createCompanyBatch,
  getCompanyBatches, getCompanyFacets, getCompanyImports, getSites, uploadCompanyImport,
} from '../api'

const STATUS: Record<string, { color: string; label: string }> = {
  selection_review: { color: 'warning', label: 'Список на согласовании' },
  running: { color: 'processing', label: 'Генерируется' },
  done: { color: 'success', label: 'Готово' },
  failed: { color: 'error', label: 'Ошибка' },
}

export default function BuildersPage() {
  const navigate = useNavigate()
  const [batches, setBatches] = useState<CompanyBatchRow[]>([])
  const [sites, setSites] = useState<SiteBrief[]>([])
  const [open, setOpen] = useState(false)
  const [facets, setFacets] = useState<Facets>({ regions: [], categories: [] })
  const [lastImport, setLastImport] = useState<CompanyImportResult | null>(null)
  const [form] = Form.useForm()

  const load = () => getCompanyBatches().then(setBatches)
  const loadLastImport = () => getCompanyImports().then(imports => setLastImport(imports[0] ?? null))

  useEffect(() => {
    load()
    getSites().then(setSites)
    loadLastImport()
  }, [])

  useEffect(() => {
    const active = batches.some(b => b.status === 'running')
    if (!active) return
    const timer = setInterval(load, 5000)
    return () => clearInterval(timer)
  }, [batches])

  const onSiteChange = async (siteId: number) => {
    setFacets(await getCompanyFacets(siteId))
    form.setFieldsValue({ region_raw: undefined, category_raw: undefined })
  }

  const submit = async (values: {
    site_id: number; region_raw: string; category_raw: string
    category_normalized: string; teaser_category_id: number
    teaser_city_id: number; teaser_location_id: number; count: number
  }) => {
    try {
      const batch = await createCompanyBatch(values)
      setOpen(false)
      form.resetFields()
      navigate(`/builders/${batch.id}`)
    } catch {
      // сообщение об ошибке уже показывает интерцептор api.ts
    }
  }

  const uploadProps: UploadProps = {
    accept: '.xlsx',
    showUploadList: false,
    customRequest: async ({ file, onSuccess, onError }) => {
      try {
        const result = await uploadCompanyImport(file as File)
        if (result.status === 'failed') {
          message.error(result.error_message || 'Не удалось разобрать файл')
        } else {
          message.success(
            `Загружено: ${result.matched_count} компаний из ${result.row_count} строк`)
        }
        loadLastImport()
        onSuccess?.(result)
      } catch (e) {
        onError?.(e as Error)
      }
    },
  }

  return (
    <>
      <Space style={{ marginBottom: 16, justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Партии строителей</Typography.Title>
        <Space>
          <Upload {...uploadProps}>
            <Button icon={<UploadOutlined />}>Загрузить выгрузку Яндекс.Карт</Button>
          </Upload>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
            Новая партия
          </Button>
        </Space>
      </Space>

      {lastImport && (
        <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
          Последняя загрузка: «{lastImport.filename}» —{' '}
          {dayjs(lastImport.uploaded_at).format('DD.MM.YYYY HH:mm')},
          компаний: {lastImport.matched_count} из {lastImport.row_count} строк
          {lastImport.status === 'failed' && (
            <Typography.Text type="danger"> — ошибка: {lastImport.error_message}</Typography.Text>
          )}
        </Typography.Paragraph>
      )}

      <Card styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="id"
          dataSource={batches}
          pagination={{ pageSize: 20 }}
          onRow={r => ({ onClick: () => navigate(`/builders/${r.id}`),
                         style: { cursor: 'pointer' } })}
          columns={[
            { title: 'Сайт', dataIndex: 'site_name' },
            { title: 'Регион', dataIndex: 'region_raw' },
            { title: 'Категория', dataIndex: 'category_raw' },
            { title: 'Компаний', dataIndex: 'requested_count', width: 100 },
            {
              title: 'Готово', width: 110,
              render: (_, r: CompanyBatchRow) =>
                `${r.companies.filter(c => c.status === 'published').length} / ${r.companies.length}`,
            },
            {
              title: 'Статус', dataIndex: 'status', width: 200,
              render: (s: string) => <Tag color={STATUS[s]?.color}>{STATUS[s]?.label ?? s}</Tag>,
            },
            {
              title: 'Создана', dataIndex: 'created_at', width: 160,
              render: (v: string) => dayjs(v).format('DD.MM.YYYY HH:mm'),
            },
          ]}
        />
      </Card>

      <Modal open={open} onCancel={() => setOpen(false)} onOk={form.submit}
             title="Новая партия строителей" okText="Отобрать компании" destroyOnHidden>
        <Form form={form} layout="vertical" onFinish={submit}
              initialValues={{ count: 10 }} requiredMark={false}>
          <Form.Item name="site_id" label="Сайт"
                     rules={[{ required: true, message: 'Выберите сайт' }]}>
            <Select placeholder="Выберите сайт" onChange={onSiteChange}
                    options={sites.map(s => ({ value: s.id, label: `${s.name} — ${s.domain}` }))} />
          </Form.Item>
          <Form.Item name="region_raw" label="Регион (из выгрузки)"
                     rules={[{ required: true, message: 'Выберите регион' }]}>
            <Select placeholder="Регион" options={facets.regions.map(r => ({ value: r, label: r }))} />
          </Form.Item>
          <Form.Item name="category_raw" label="Категория (из выгрузки)"
                     rules={[{ required: true, message: 'Выберите категорию' }]}>
            <Select placeholder="Категория"
                    options={facets.categories.map(c => ({ value: c, label: c }))} />
          </Form.Item>
          <Form.Item name="category_normalized" label="Название сферы для этого сайта"
                     rules={[{ required: true, message: 'Укажите нормализованное имя' }]}>
            <Select mode="tags" maxCount={1} placeholder="Например: Дома под ключ" />
          </Form.Item>
          <Space.Compact block>
            <Form.Item name="teaser_category_id" label="Category ID" style={{ width: '33%' }}
                       rules={[{ required: true, message: 'ID' }]}>
              <InputNumber min={1} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="teaser_city_id" label="City ID" style={{ width: '33%' }}
                       rules={[{ required: true, message: 'ID' }]}>
              <InputNumber min={1} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="teaser_location_id" label="Location ID" style={{ width: '34%' }}
                       rules={[{ required: true, message: 'ID' }]}>
              <InputNumber min={1} style={{ width: '100%' }} />
            </Form.Item>
          </Space.Compact>
          <Form.Item name="count" label="Сколько компаний"
                     rules={[{ required: true, message: 'Укажите количество' }]}>
            <InputNumber min={1} max={50} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
