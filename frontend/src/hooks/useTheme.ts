import { useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(
    () =>
      (localStorage.getItem('theme') as Theme | null) ??
      (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'),
  )

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])

  return { theme, toggle: () => setTheme((t) => (t === 'dark' ? 'light' : 'dark')) }
}

/** Re-renders the caller whenever `data-theme` changes, so chart colors picked up via cssVar() stay in sync. */
export function useThemeTick() {
  const [tick, setTick] = useState(0)

  useEffect(() => {
    const observer = new MutationObserver(() => setTick((t) => t + 1))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => observer.disconnect()
  }, [])

  return tick
}

export const cssVar = (name: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim()
