import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import ruRU from 'antd/locale/ru_RU'
import dayjs from 'dayjs'
// Локаль antd задаёт подписи самих компонентов, но даты форматирует dayjs со
// своей — и без этой строки «13 сентября» на странице доработок выходило бы
// «13 September». Остальные экраны печатают даты числами (DD.MM.YYYY), так что
// переключение на них не влияет.
import 'dayjs/locale/ru'
import App from './App'
import { AuthProvider } from './auth'
import './index.css'

dayjs.locale('ru')

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={ruRU}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </ConfigProvider>
  </React.StrictMode>,
)
