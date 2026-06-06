import { createContext, useContext, useState } from 'react'
import type { ReactNode } from 'react'
import type { SettingsPayload } from '../types/api'

interface AppState {
  settings: SettingsPayload
  scopes: string[]
  knownProducts: string[]
}

interface AppContextType extends AppState {
  setSettings: (s: SettingsPayload) => void
  setScopes: (scopes: string[]) => void
  addProducts: (products: string[]) => void
}

const defaults: AppState = {
  settings: { base_url: '', api_key: '', team: '', ollama_url: 'http://localhost:11434' },
  scopes: [],
  knownProducts: [],
}

const AppContext = createContext<AppContextType>({
  ...defaults,
  setSettings: () => undefined,
  setScopes: () => undefined,
  addProducts: () => undefined,
})

export function AppProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<SettingsPayload>(defaults.settings)
  const [scopes, setScopes] = useState<string[]>([])
  const [knownProducts, setKnownProducts] = useState<string[]>([])

  const addProducts = (products: string[]) => {
    setKnownProducts((prev) => {
      const s = new Set([...prev, ...products])
      return Array.from(s)
    })
  }

  return (
    <AppContext.Provider value={{ settings, scopes, knownProducts, setSettings, setScopes, addProducts }}>
      {children}
    </AppContext.Provider>
  )
}

export const useApp = () => useContext(AppContext)
