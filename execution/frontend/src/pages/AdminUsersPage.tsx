import { useEffect, useState } from 'react'
import {
  Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Typography,
} from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { UserRow, createUser, deleteUser, getUsers, updateUser } from '../api'

export default function AdminUsersPage() {
  const [users, setUsers] = useState<UserRow[]>([])
  const [editing, setEditing] = useState<UserRow | null>(null)
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()

  const load = () => getUsers().then(setUsers)
  useEffect(() => { load() }, [])

  const openForm = (user: UserRow | null) => {
    setEditing(user)
    setOpen(true)
  }

  // Найдено при ручной проверке (тот же дефект, что и в AdminSitesPage.tsx,
  // Task 24): с destroyOnHidden Modal размонтирует <Form>, пока закрыт, и
  // form.resetFields()/setFieldsValue(), вызванные синхронно в openForm() до
  // применения setOpen, попадали на ещё не подключенный к DOM экземпляр формы
  // — antd молча принимал значения, но ругался в консоль ("Instance created
  // by useForm is not connected to any Form element"). Функционально работало
  // (поля заполнялись верно), но засоряло консоль при каждом открытии модалки.
  // Перенесено в эффект по open — Modal к этому моменту уже монтирует форму
  // в том же цикле рендера.
  useEffect(() => {
    if (!open) return
    form.resetFields()
    form.setFieldsValue(editing ? { ...editing, password: '' }
                                 : { role: 'manager', is_active: true })
  }, [open])

  const submit = async (values: Record<string, unknown>) => {
    try {
      if (editing) await updateUser(editing.id, values)
      else await createUser(values)
      setOpen(false)
      load()
    } catch {
      // Устоявшийся в проекте паттерн (Task 21-24): onFinish не перехватывается
      // самой antd-формой — без catch отказ createUser/updateUser (например,
      // «это последний активный администратор» или занятый email) стал бы
      // unhandled rejection, хотя текст ошибки уже показан глобальным
      // интерцептором api.ts. Модалка остаётся открытой с заполненной формой.
    }
  }

  return (
    <>
      <Space style={{ marginBottom: 16, justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>Пользователи</Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => openForm(null)}>
          Добавить
        </Button>
      </Space>

      <Card styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="id" dataSource={users} pagination={false}
          columns={[
            { title: 'Email', dataIndex: 'email' },
            { title: 'Имя', dataIndex: 'full_name' },
            {
              title: 'Роль', dataIndex: 'role', width: 140,
              render: (r: string) => (
                <Tag color={r === 'admin' ? 'gold' : 'default'}>
                  {r === 'admin' ? 'Администратор' : 'Менеджер'}
                </Tag>
              ),
            },
            {
              title: 'Активен', dataIndex: 'is_active', width: 100,
              render: (v: boolean) => v ? 'да' : 'нет',
            },
            {
              title: '', width: 160,
              render: (_, r: UserRow) => (
                <Space>
                  <Button size="small" type="link" onClick={() => openForm(r)}>Правка</Button>
                  {/* Обязательная находка №3 (тот же класс, что и удаление сайта
                      в AdminSitesPage.tsx, Task 24): было мгновенное необратимое
                      удаление одним кликом по маленькой ссылке в плотной строке
                      таблицы. delete_user (app/api/admin_users.py) — жёсткий
                      db.delete без мягкого удаления и без восстановления из
                      интерфейса; единственная защита на бэкенде — от удаления
                      последнего активного администратора, обычного менеджера
                      или не-последнего админа ничто раньше не защищало на
                      фронте. */}
                  <Popconfirm
                    title="Удалить пользователя?"
                    description={<>
                      Учётная запись {r.email} будет удалена безвозвратно. Партии
                      и задачи, которые он создавал, останутся в журнале без
                      привязки к автору.
                    </>}
                    okText="Удалить" okType="danger" cancelText="Отмена"
                    onConfirm={async () => { await deleteUser(r.id); load() }}
                  >
                    <Button size="small" type="link" danger>Удалить</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal open={open} onCancel={() => setOpen(false)} onOk={form.submit}
             title={editing ? 'Изменение пользователя' : 'Новый пользователь'} destroyOnHidden>
        <Form form={form} layout="vertical" onFinish={submit} requiredMark={false}>
          <Form.Item name="email" label="Email" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="full_name" label="Имя" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="role" label="Роль">
            <Select options={[{ value: 'manager', label: 'Менеджер' },
                              { value: 'admin', label: 'Администратор' }]} />
          </Form.Item>
          <Form.Item name="password" label="Пароль"
                     extra={editing ? 'Пусто — оставить текущий' : 'Минимум 8 символов'}
                     rules={[{ required: !editing, message: 'Введите пароль' }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="is_active" label="Активен" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
