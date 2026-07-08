import { useEffect, useState } from 'react'
import { Eye, EyeOff, CheckCircle, ShieldCheck, XCircle, Loader } from 'lucide-react'
import { saveSettings, testConnection, getSettings, getOllamaModels } from '../api'
import { errorMessage } from '../api/client'
import { useApp } from '../context/AppContext'
import { usePageState } from '../context/PageStateContext'

export function SettingsPage() {
  const { settings, setSettings, setScopes } = useApp()
  const { settingsDraft, patchSettingsDraft } = usePageState()
  const [showKey, setShowKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [models, setModels] = useState<string[]>([])

  const { form, saveMsg, testResult, testError } = settingsDraft

  // Seed the draft from the backend + app context only the first time this
  // session touches Settings — once a draft exists, keep it exactly as the
  // user left it (including unsaved edits) when they come back from another tab.
  useEffect(() => {
    getOllamaModels().then((r) => setModels(r.data.models)).catch(() => {})
    if (form) return
    getSettings()
      .then((r) => {
        patchSettingsDraft({
          form: {
            ...settings,
            base_url: r.data.base_url,
            team: r.data.team ?? '',
            ollama_url: r.data.ollama_url || settings.ollama_url,
            ollama_model: r.data.ollama_model || settings.ollama_model || '',
            has_api_key: r.data.has_api_key,
          },
        })
      })
      .catch(() => patchSettingsDraft({ form: settings }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!form) {
    return (
      <>
        <div className="page-header">
          <div className="page-title">Settings</div>
          <div className="page-subtitle">Configure your Stack Overflow Enterprise connection</div>
        </div>
        <div className="card" style={{ maxWidth: 620 }}>
          <div className="text-muted text-sm">Loading…</div>
        </div>
      </>
    )
  }

  const setForm = (next: typeof form) => patchSettingsDraft({ form: next })
  const field = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [key]: e.target.value })

  const handleSave = async () => {
    setSaving(true)
    patchSettingsDraft({ saveMsg: null })
    try {
      const res = await saveSettings(form)
      const updated = { ...form, has_api_key: res.data.has_api_key, api_key: '' }
      setForm(updated)
      setSettings({ ...settings, ...form })
      patchSettingsDraft({
        saveMsg: { ok: true, text: res.data.has_api_key ? 'Settings saved — API key is set.' : 'Settings saved.' },
      })
    } catch (err) {
      patchSettingsDraft({ saveMsg: { ok: false, text: errorMessage(err) } })
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    patchSettingsDraft({ testResult: null, testError: null })
    // Save first so the backend uses current values
    try {
      const res = await saveSettings(form)
      setForm({ ...form, has_api_key: res.data.has_api_key, api_key: '' })
      setSettings({ ...settings, ...form })
    } catch {
      // continue even if save fails — test with whatever backend has
    }
    try {
      const res = await testConnection()
      patchSettingsDraft({ testResult: res.data })
      if (res.data.scopes) setScopes(res.data.scopes)
    } catch (err) {
      patchSettingsDraft({ testError: errorMessage(err) })
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
          {form.has_api_key && (
            <div className="text-sm" style={{ color: 'var(--success)', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
              <ShieldCheck size={13} /> A key is currently saved on the server — leave this blank to keep it.
            </div>
          )}
          <div className="pw-wrap">
            <input
              className="input"
              type={showKey ? 'text' : 'password'}
              placeholder={form.has_api_key ? 'Leave blank to keep the saved key' : '••••••••••••••••'}
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
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>Classification model <span className="hint">(installed Ollama models)</span></label>
            {models.length > 0 ? (
              <select
                className="input"
                value={form.ollama_model || ''}
                onChange={(e) => setForm({ ...form, ollama_model: e.target.value })}
              >
                <option value="">— keep current —</option>
                {models.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            ) : (
              <input
                className="input"
                placeholder="e.g. llama3.1:8b"
                value={form.ollama_model || ''}
                onChange={(e) => setForm({ ...form, ollama_model: e.target.value })}
              />
            )}
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
