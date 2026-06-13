import { createContext, useContext, useState, useEffect } from 'react'
import type { ReactNode } from 'react'
import type { SettingsPayload } from '../types/api'
import { getSettings } from '../api'

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
  settings: { base_url: '', api_key: '', team: '', ollama_url: 'http://localhost:11434', ollama_model: '' },
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

  useEffect(() => {
    getSettings()
      .then((r) => {
        const d = r.data
        if (d.base_url) setSettings((prev) => ({ ...prev, base_url: d.base_url, team: d.team ?? '', ollama_url: d.ollama_url ?? prev.ollama_url }))
        if (d.default_tags) {
          const tags = d.default_tags.split(',').map((t: string) => t.trim()).filter(Boolean)
          setKnownProducts((prev) => Array.from(new Set([...prev, ...tags])))
        }
      })
      .catch(() => {})
  }, [])

  return (
    <AppContext.Provider value={{ settings, scopes, knownProducts, setSettings, setScopes, addProducts }}>
      {children}
    </AppContext.Provider>
  )
}

export const useApp = () => useContext(AppContext)
