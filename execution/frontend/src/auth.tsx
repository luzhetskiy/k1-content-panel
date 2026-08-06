import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { Spin } from 'antd'
import { me, Profile } from './api'

interface AuthState {
  profile: Profile | null
  setProfile: (p: Profile | null) => void
  isAdmin: boolean
}

const AuthContext = createContext<AuthState>({
  profile: null, setProfile: () => {}, isAdmin: false,
})

export const useAuth = () => useContext(AuthContext)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Cookie httpOnly из JS не читается — единственный способ узнать, жива ли
    // сессия, это спросить бэкенд. Ожидаемый 401 здесь (пользователь ещё не
    // залогинен, в том числе на /login) интерцептор api.ts обрабатывает молча —
    // см. комментарий там же.
    me().then(setProfile).catch(() => setProfile(null)).finally(() => setLoading(false))
  }, [])

  if (loading) {
    return <div style={{ padding: 80, textAlign: 'center' }}><Spin size="large" /></div>
  }

  return (
    <AuthContext.Provider value={{ profile, setProfile, isAdmin: profile?.role === 'admin' }}>
      {children}
    </AuthContext.Provider>
  )
}
