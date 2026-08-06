import axios from 'axios'
import { message } from 'antd'

const api = axios.create({ baseURL: '/api' })

api.interceptors.response.use(
  r => r,
  error => {
    const status = error.response?.status
    const onLoginPage = location.pathname.startsWith('/login')
    if (status === 401 && !onLoginPage) {
      location.href = '/login'
    } else if (status === 401 && onLoginPage) {
      // Ожидаемый случай, а не ошибка: AuthProvider (auth.tsx) при каждом монтировании
      // зовёт me(), чтобы узнать, жива ли сессия — cookie httpOnly из JS не читается,
      // это единственный способ спросить бэкенд. При обычном первом открытии /login
      // неавторизованным пользователем бэкенд закономерно отвечает 401
      // (get_current_user, app/api/deps.py), и это не повод показывать тост с ошибкой.
      // Без этой ветки 401 на /login проваливался бы в общий else ниже и красный
      // тост «Ошибка сервера» всплывал бы при каждом обычном заходе на страницу входа.
    } else if (status === 403) {
      message.error('Нет доступа')
    } else {
      message.error(error.response?.data?.detail ?? 'Ошибка сервера')
    }
    return Promise.reject(error)
  },
)

export interface Profile { email: string; full_name: string; role: string }
export interface SiteBrief {
  id: number; name: string; domain: string; publish_target: string
  url_prefix: string; reference_images: number; is_ready: boolean
}
export interface SiteFull {
  id: number; name: string; domain: string; base_url: string
  api_token: string; is_active: boolean; publish_target: string
  site_description: string; tone_of_voice: string
  articles_parent_id: number | null; reference_article_id: number | null
  image_style_prompt: string; cover_mode: string; cover_style_prompt: string
  builder_template_html: string; builder_parent_id: number | null
  teaser_category_id: number | null; teaser_city_id: number | null
  teaser_location_id: number | null; watermark_path: string
  // Заполняются синхронизацией, в форме только читаются.
  articles_url_prefix: string; reference_images: number
  reference_synced_at: string | null
}
export interface ArticleRow {
  id: number; topic: string; title: string; status: string
  remote_url: string; error_text: string
}
export interface Batch {
  id: number; site_id: number; site_name: string; site_domain: string
  requested_count: number; status: string; error_text: string
  created_at: string; articles: ArticleRow[]
}
export interface Prompt { id: number; key: string; site_id: number | null; text: string }
export interface JobRow {
  id: number; kind: string; site_name: string; status: string; log_text: string
  cost: number; tokens_total: number; started_at: string; finished_at: string | null
}
export interface UserRow {
  id: number; email: string; full_name: string; role: string; is_active: boolean
}
// Бэкенд (_current_settings, app/api/admin_settings.py) обычно отдаёт плоский
// набор строк, но если секретную настройку не удалось расшифровать (например,
// ENCRYPTION_KEY сменился), в ответ добавляется ключ "_errors" — это не строка,
// а вложенный объект { ключ_настройки: текст_ошибки }. Плоский
// Record<string, string> эту форму не описывает: TypeScript считал бы
// settings['_errors'] строкой, хотя по факту это объект, и код читающий это
// поле как строку упал бы в рантайме. Пересечение типов явно выделяет
// "_errors" как опциональное поле другой формы, не трогая остальные ключи.
export type SettingsMap = Record<string, string> & { _errors?: Record<string, string> }

export const login = (email: string, password: string) => {
  const form = new URLSearchParams({ username: email, password })
  return api.post<Profile>('/auth/login', form).then(r => r.data)
}
export const logout = () => api.post('/auth/logout')
export const me = () => api.get<Profile>('/auth/me').then(r => r.data)

export const getSites = () => api.get<SiteBrief[]>('/sites').then(r => r.data)
export const getAdminSites = () => api.get<SiteFull[]>('/admin/sites').then(r => r.data)
export const createSite = (d: Partial<SiteFull>) =>
  api.post<SiteFull>('/admin/sites', d).then(r => r.data)
export const updateSite = (id: number, d: Partial<SiteFull>) =>
  api.put<SiteFull>(`/admin/sites/${id}`, d).then(r => r.data)
export const deleteSite = (id: number) => api.delete(`/admin/sites/${id}`)
export const syncSite = (id: number) =>
  api.post<{ ok: boolean; url_prefix: string; pages: number
             reference_images: number; detail: string }>(`/admin/sites/${id}/sync`)
    .then(r => r.data)
export const uploadWatermark = (id: number, file: File) => {
  const form = new FormData()
  form.append('file', file)
  return api.post(`/admin/sites/${id}/watermark`, form)
}

export const getBatches = () => api.get<Batch[]>('/article-batches').then(r => r.data)
export const getBatch = (id: number) =>
  api.get<Batch>(`/article-batches/${id}`).then(r => r.data)
export const createBatch = (site_id: number, count: number) =>
  api.post<Batch>('/article-batches', { site_id, count }).then(r => r.data)
export const saveTopics = (id: number, topics: string[]) =>
  api.put<Batch>(`/article-batches/${id}/topics`, { topics }).then(r => r.data)
export const runBatch = (id: number) =>
  api.post<Batch>(`/article-batches/${id}/run`).then(r => r.data)
export const retryArticle = (id: number) => api.post(`/articles/${id}/retry`)

export const getSettings = () => api.get<SettingsMap>('/admin/settings').then(r => r.data)
export const updateSettings = (d: Record<string, string>) =>
  api.put<SettingsMap>('/admin/settings', d).then(r => r.data)

export const getPrompts = () => api.get<Prompt[]>('/admin/prompts').then(r => r.data)
export const savePrompt = (d: Partial<Prompt>) =>
  api.put<Prompt>('/admin/prompts', d).then(r => r.data)
export const testPrompt = (text: string, variables: Record<string, unknown>) =>
  api.post<{ rendered: string; answer: string; tokens_total: number; cost: number }>(
    '/admin/prompts/test', { text, variables }).then(r => r.data)

export const getJobs = () => api.get<JobRow[]>('/jobs').then(r => r.data)

export const getUsers = () => api.get<UserRow[]>('/admin/users').then(r => r.data)
export const createUser = (d: Record<string, unknown>) =>
  api.post<UserRow>('/admin/users', d).then(r => r.data)
export const updateUser = (id: number, d: Record<string, unknown>) =>
  api.put<UserRow>(`/admin/users/${id}`, d).then(r => r.data)
export const deleteUser = (id: number) => api.delete(`/admin/users/${id}`)

export default api
