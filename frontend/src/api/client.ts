import axios from 'axios'

export const api = axios.create({
  baseURL: '/',
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
})

// Normalise error messages so callers get a plain string.
export function errorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) return detail.map((d) => d.msg ?? String(d)).join('; ')
    if (err.response?.statusText) return `${err.response.status} ${err.response.statusText}`
    return err.message
  }
  if (err instanceof Error) return err.message
  return String(err)
}
