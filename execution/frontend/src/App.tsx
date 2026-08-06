import { useEffect, useState, ReactNode } from 'react'
import { BrowserRouter, Routes, Route, NavLink, Navigate, useLocation } from 'react-router-dom'
import { ConfigProvider, Layout, Menu, Drawer, Button, Dropdown } from 'antd'
import {
  FileTextOutlined, HomeOutlined, GlobalOutlined, BulbOutlined,
  SettingOutlined, TeamOutlined, HistoryOutlined, MenuOutlined,
  CloseOutlined, UserOutlined,
} from '@ant-design/icons'
import { useAuth } from './auth'
import { logout } from './api'
import LoginPage from './pages/LoginPage'
import ArticlesPage from './pages/ArticlesPage'
import BatchPage from './pages/BatchPage'
import JobsPage from './pages/JobsPage'
import AdminSitesPage from './pages/AdminSitesPage'
import AdminPromptsPage from './pages/AdminPromptsPage'
import AdminSettingsPage from './pages/AdminSettingsPage'
import AdminUsersPage from './pages/AdminUsersPage'

const { Sider, Content } = Layout

const navItems = [
  { key: '/articles', label: 'Статьи', icon: <FileTextOutlined />, admin: false },
  { key: '/builders', label: 'Строители', icon: <HomeOutlined />, admin: false },
  { key: '/jobs', label: 'Журнал', icon: <HistoryOutlined />, admin: false },
  { key: '/admin/sites', label: 'Сайты', icon: <GlobalOutlined />, admin: true },
  { key: '/admin/prompts', label: 'Промпты', icon: <BulbOutlined />, admin: true },
  { key: '/admin/settings', label: 'Настройки', icon: <SettingOutlined />, admin: true },
  { key: '/admin/users', label: 'Пользователи', icon: <TeamOutlined />, admin: true },
]

// Палитра перенесена из nst-tg-monitor без изменений: производные оттенки там
// подобраны вручную, а не сгенерированы из colorPrimary алгоритмом.
const antTheme = {
  token: {
    colorPrimary: '#dca34c',
    colorBgContainer: '#ffffff',
    colorBgLayout: '#f4f4f5',
    borderRadius: 8,
    borderRadiusLG: 12,
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    colorTextHeading: '#18181b',
    colorText: '#3f3f46',
    colorTextSecondary: '#71717a',
    colorBorder: '#e4e4e7',
    colorBorderSecondary: '#f0f0f0',
    boxShadow: '0 1px 3px 0 rgb(0 0 0 / 0.06), 0 1px 2px -1px rgb(0 0 0 / 0.06)',
    boxShadowSecondary: '0 4px 6px -1px rgb(0 0 0 / 0.07), 0 2px 4px -2px rgb(0 0 0 / 0.07)',
  },
  components: {
    Layout: { siderBg: '#ffffff', bodyBg: '#f4f4f5' },
    Menu: {
      itemBg: 'transparent', itemSelectedBg: '#fef8ee', itemSelectedColor: '#dca34c',
      itemActiveBg: '#fef8ee', itemHoverBg: '#f4f4f5', itemHoverColor: '#18181b',
      itemColor: '#52525b', iconSize: 15,
    },
    Card: { borderRadius: 12 },
    Button: { borderRadius: 8 },
    Table: { headerBg: '#fafafa', borderRadius: 12 },
  },
}

const logoBlock = (
  <div style={{
    padding: '18px 16px 16px', display: 'flex', alignItems: 'center', gap: 10,
    borderBottom: '1px solid #f0f0f0', marginBottom: 8,
  }}>
    <div style={{
      width: 30, height: 30, borderRadius: 8,
      background: 'linear-gradient(135deg, #dca34c 0%, #e8b96a 100%)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontSize: 14, fontWeight: 700, color: '#fff', flexShrink: 0, letterSpacing: '-0.5px',
    }}>K1</div>
    <div>
      <div style={{ fontWeight: 600, fontSize: 13, color: '#18181b', lineHeight: 1.2 }}>
        Контент-сервис
      </div>
      <div style={{ fontSize: 11, color: '#a1a1aa', lineHeight: 1.2 }}>
        Статьи и строители
      </div>
    </div>
  </div>
)

export default function App() {
  return (
    <ConfigProvider theme={antTheme}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="*" element={<Shell />} />
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  )
}

// Пункты меню уже скрыты от не-admin (items ниже), но прямой переход по URL
// (например, менеджер набрал /admin/sites руками или перешёл по старой
// ссылке) ничем на уровне роутинга раньше не блокировался — <Routes> внутри
// Shell() рендерил страницу безусловно. Дыры в безопасности в этом нет:
// бэкенд проверяет роль на каждый запрос (require_role("admin")) и вернул бы
// 403 на все данные страницы. Но без редиректа менеджер сначала видел бы
// пустую оболочку админской страницы и тост «Нет доступа» из интерцептора
// api.ts на каждый её запрос — редирект на /articles до рендера страницы
// дешевле и опрятнее для UX, поэтому добавлен уже в каркасе, а не отложен.
function AdminRoute({ children }: { children: ReactNode }) {
  const { isAdmin } = useAuth()
  return isAdmin ? <>{children}</> : <Navigate to="/articles" replace />
}

function Shell() {
  const { profile, isAdmin } = useAuth()
  const [isMobile, setIsMobile] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 767px)')
    setIsMobile(mq.matches)
    const handler = (e: MediaQueryListEvent) => {
      setIsMobile(e.matches)
      if (!e.matches) setDrawerOpen(false)
    }
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  if (!profile) return <Navigate to="/login" replace />

  const items = navItems.filter(i => !i.admin || isAdmin)

  const userMenu = (
    <Dropdown menu={{
      items: [{
        key: 'logout', label: 'Выйти',
        onClick: async () => { await logout(); location.href = '/login' },
      }],
    }}>
      <Button type="text" icon={<UserOutlined />} style={{ color: '#52525b' }}>
        {profile.full_name}
      </Button>
    </Dropdown>
  )

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {!isMobile && (
        <Sider width={220} style={{
          background: '#fff', borderRight: '1px solid #e4e4e7',
          position: 'sticky', top: 0, height: '100vh', overflow: 'auto',
        }}>
          {logoBlock}
          <SideNav items={items} />
        </Sider>
      )}

      {isMobile && (
        <Drawer
          open={drawerOpen} onClose={() => setDrawerOpen(false)} placement="left" width={220}
          closeIcon={<CloseOutlined style={{ fontSize: 14, color: '#71717a' }} />}
          styles={{
            body: { padding: 0 },
            header: { padding: '14px 16px', borderBottom: '1px solid #f0f0f0', minHeight: 'auto' },
            mask: { background: 'rgb(0 0 0 / 0.35)' },
          }}
          title="Контент-сервис"
        >
          <div style={{ paddingTop: 8 }}>
            <SideNav items={items} onNavigate={() => setDrawerOpen(false)} />
          </div>
        </Drawer>
      )}

      <Layout style={{ background: '#f4f4f5' }}>
        <div style={{
          position: 'sticky', top: 0, zIndex: 100, background: '#fff',
          borderBottom: '1px solid #e4e4e7', padding: '0 16px', height: 52,
          display: 'flex', alignItems: 'center', gap: 12, justifyContent: 'space-between',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            {isMobile && (
              <Button type="text" icon={<MenuOutlined />} onClick={() => setDrawerOpen(true)}
                      style={{ color: '#52525b', width: 36, height: 36, padding: 0 }} />
            )}
            <span style={{ fontWeight: 600, fontSize: 14, color: '#18181b' }}>
              Контент-сервис
            </span>
          </div>
          {userMenu}
        </div>

        <Content style={{ padding: isMobile ? 16 : 28 }}>
          <Routes>
            <Route path="/" element={<Navigate to="/articles" replace />} />
            <Route path="/articles" element={<ArticlesPage />} />
            <Route path="/articles/:id" element={<BatchPage />} />
            <Route path="/builders" element={
              <div style={{ color: '#71717a' }}>
                Раздел «Строители» появится в плане 2. Пока процесс идёт через CLI.
              </div>
            } />
            <Route path="/jobs" element={<JobsPage />} />
            <Route path="/admin/sites" element={<AdminRoute><AdminSitesPage /></AdminRoute>} />
            <Route path="/admin/prompts" element={<AdminRoute><AdminPromptsPage /></AdminRoute>} />
            <Route path="/admin/settings" element={<AdminRoute><AdminSettingsPage /></AdminRoute>} />
            <Route path="/admin/users" element={<AdminRoute><AdminUsersPage /></AdminRoute>} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  )
}

function SideNav({ items, onNavigate }: {
  items: typeof navItems; onNavigate?: () => void
}) {
  const { pathname } = useLocation()
  return (
    <Menu
      mode="inline"
      selectedKeys={[items.find(i => pathname.startsWith(i.key))?.key ?? pathname]}
      onClick={onNavigate}
      style={{ border: 'none', background: 'transparent', padding: '0 8px' }}
      items={items.map(item => ({
        key: item.key, icon: item.icon,
        label: <NavLink to={item.key} style={{ textDecoration: 'none' }}>{item.label}</NavLink>,
      }))}
    />
  )
}
