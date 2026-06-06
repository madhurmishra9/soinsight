import { useState } from 'react'
import { Eye, EyeOff, CheckCircle, XCircle, Loader } from 'lucide-react'
import { saveSettings, testConnection } from '../api'
import { errorMessage } from '../api/client'
import { useApp } from '../context/AppContext'
import type { ConnectionTestResult } from '../types/api'

export function SettingsPage() {
  const { settings, setSettings, setScopes } = useApp()
  const [form, setForm] = useState(settings)
  const [showKey, setShowKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null)
  const [testError, setTestError] = useState<string | null>(null)

  const field = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }))

  const handleSave = async () => {
    setSaving(true)
    setSaveMsg(null)
    try {
      await saveSettings(form)
      setSettings(form)
      setSaveMsg({ ok: true, text: 'Settings saved.' })
    } catch (err) {
      setSaveMsg({ ok: false, text: errorMessage(err) })
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    setTestError(null)
    // Save first so the backend uses current values
    try {
      await saveSettings(form)
      setSettings(form)
    } catch {
      // continue even if save fails — test with whatever backend has
    }
    try {
      const res = await testConnection()
      setTestResult(res.data)
      if (res.data.scopes) setScopes(res.data.scopes)
    } catch (err) {
      setTestError(errorMessage(err))
    } finally {
      setTesting(false)
    }
  }

  return (
    <>
      <div className="page-header">
        <div className="page-title">Settings</div>
        <div className="page-subtitle">Configure your Stack Overflow Enterprise connection</div>
      </div>

      <div className="card" style={{ maxWidth: 620 }}>
        <div className="card-title">Instance connection</div>

        <div className="form-group">
          <label>Base URL</label>
          <input
            className="input"
            type="url"
            placeholder="https://your-instance.stackenterprise.co"
            value={form.base_url}
            onChange={field('base_url')}
          />
        </div>

        <div className="form-group">
          <label>API Key <span className="hint">(stored in memory only)</span></label>
          <div className="pw-wrap">
            <input
              className="input"
              type={showKey ? 'text' : 'password'}
              placeholder="••••••••••••••••"
              value={form.api_key}
              onChange={field('api_key')}
              autoComplete="off"
            />
            <button className="pw-toggle" type="button" onClick={() => setShowKey((v) => !v)}>
              {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </div>

        <div className="form-row">
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>Team / Scope <span className="hint">(optional)</span></label>
            <input
              className="input"
              placeholder="e.g. my-team"
              value={form.team}
              onChange={field('team')}
            />
          </div>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>Ollama URL</label>
            <input
              className="input"
              placeholder="http://localhost:11434"
              value={form.ollama_url}
              onChange={field('ollama_url')}
            />
          </div>
        </div>

        {saveMsg && (
          <div className={`alert ${saveMsg.ok ? 'alert-success' : 'alert-error'}`} style={{ marginTop: 16, marginBottom: 0 }}>
            {saveMsg.ok ? <CheckCircle size={14} /> : <XCircle size={14} />}
            {saveMsg.text}
          </div>
        )}

        <div className="btn-group" style={{ marginTop: 20 }}>
          <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? <Loader size={14} className="spin" /> : null}
            Save settings
          </button>
          <button className="btn btn-secondary" onClick={handleTest} disabled={testing}>
            {testing ? <Loader size={14} className="spin" /> : null}
            Test connection
          </button>
        </div>
      </div>

      {(testResult ?? testError) && (
        <div className="card" style={{ maxWidth: 620, marginTop: 16 }}>
          <div className="card-title">Connection result</div>

          {testError && (
            <div className="alert alert-error">
              <XCircle size={14} /> {testError}
            </div>
          )}

          {testResult && (
            <>
              <div className="flex items-center gap-8 mb-16">
                {testResult.reachable ? (
                  <span className="badge badge-green"><CheckCircle size={12} /> Reachable</span>
                ) : (
                  <span className="badge badge-red"><XCircle size={12} /> Unreachable</span>
                )}
                {testResult.version && (
                  <span className="badge badge-blue">v{testResult.version}</span>
                )}
              </div>

              {testResult.scopes.length > 0 && (
                <>
                  <div className="text-sm text-muted" style={{ marginBottom: 6 }}>
                    Available scopes / teams ({testResult.scopes.length})
                  </div>
                  <div className="scope-list">
                    {testResult.scopes.map((s) => (
                      <span key={s} className="badge badge-blue">{s}</span>
                    ))}
                  </div>
                </>
              )}

              {testResult.error && (
                <div className="alert alert-warning" style={{ marginTop: 12 }}>
                  {testResult.error}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </>
  )
}
