import { useEffect, useState } from 'react'
import { Eye, EyeOff, CheckCircle, Clock, PlayCircle, ShieldCheck, XCircle, Loader } from 'lucide-react'
import {
  saveSettings, testConnection, getSettings, getOllamaModels,
  getSchedule, saveSchedule, getScheduleStatus, triggerScheduleNow,
} from '../api'
import { errorMessage } from '../api/client'
import { useApp } from '../context/AppContext'
import { usePageState } from '../context/PageStateContext'
import type { ScheduleConfigPayload } from '../types/api'

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

const DEFAULT_SCHEDULE: ScheduleConfigPayload = {
  enabled: false, interval_hours: 24, products: [], window_days: 30,
}

/** Scheduled auto-fetch: runs Fetch -> Analysis -> Aggregate on an interval so
 *  data stays fresh without visiting those pages manually. The backend already
 *  runs this loop unconditionally (services/scheduler.py); this section is
 *  purely the control surface for the schedule_config row it polls. */
function ScheduleSection() {
  const { knownProducts } = useApp()
  const { schedule, patchSchedule } = usePageState()
  const { form, status, saveMsg, loaded } = schedule
  const [saving, setSaving] = useState(false)
  const [triggering, setTriggering] = useState(false)

  const loadStatus = async () => {
    try {
      const r = await getScheduleStatus()
      patchSchedule({ status: r.data })
    } catch {
      // status is best-effort — leave whatever we last had
    }
  }

  useEffect(() => {
    if (!loaded) {
      getSchedule()
        .then((r) => {
          patchSchedule({
            form: {
              enabled: r.data.enabled, interval_hours: r.data.interval_hours,
              products: r.data.products, window_days: r.data.window_days,
            },
            status: {
              enabled: r.data.enabled, running: false,
              last_run_at: r.data.last_run_at, next_run_at: r.data.next_run_at,
            },
            loaded: true,
          })
        })
        .catch(() => patchSchedule({ form: DEFAULT_SCHEDULE, loaded: true }))
    }
    void loadStatus()
    const id = setInterval(() => void loadStatus(), 30000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!form) {
    return (
      <div className="card" style={{ maxWidth: 620, marginTop: 16 }}>
        <div className="text-muted text-sm">Loading…</div>
      </div>
    )
  }

  const setForm = (next: ScheduleConfigPayload) => patchSchedule({ form: next })
  const toggleProduct = (p: string) => setForm({
    ...form,
    products: form.products.includes(p) ? form.products.filter((x) => x !== p) : [...form.products, p],
  })

  const handleSave = async () => {
    setSaving(true)
    patchSchedule({ saveMsg: null })
    try {
      const res = await saveSchedule(form)
      patchSchedule({
        form: {
          enabled: res.data.enabled, interval_hours: res.data.interval_hours,
          products: res.data.products, window_days: res.data.window_days,
        },
        status: {
          enabled: res.data.enabled, running: status?.running ?? false,
          last_run_at: res.data.last_run_at, next_run_at: res.data.next_run_at,
        },
        saveMsg: { ok: true, text: 'Schedule saved.' },
      })
    } catch (err) {
      patchSchedule({ saveMsg: { ok: false, text: errorMessage(err) } })
    } finally {
      setSaving(false)
    }
  }

  const handleTrigger = async () => {
    setTriggering(true)
    patchSchedule({ saveMsg: null })
    // Save first so a run-now always reflects whatever's currently on screen,
    // not the last-saved config — mirrors the "Test connection" save-then-act pattern above.
    try {
      await saveSchedule(form)
    } catch {
      // continue — trigger will report its own error below if this really mattered
    }
    try {
      await triggerScheduleNow()
      patchSchedule({ saveMsg: { ok: true, text: 'Triggered — running now in the background.' } })
    } catch (err) {
      patchSchedule({ saveMsg: { ok: false, text: errorMessage(err) } })
    } finally {
      setTriggering(false)
      void loadStatus()
    }
  }

  return (
    <div className="card" style={{ maxWidth: 620, marginTop: 16 }}>
      <div className="card-title">Scheduled auto-fetch</div>
      <div className="card-subtitle">
        Runs Fetch → Analysis → Aggregate automatically on an interval, so data stays
        fresh without visiting those pages manually.
      </div>

      <div className="form-group">
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
          />
          <span>Enable scheduled auto-fetch</span>
        </label>
      </div>

      <div className="form-row">
        <div className="form-group" style={{ marginBottom: 0 }}>
          <label>Interval (hours)</label>
          <input
            className="input"
            type="number"
            min={1}
            max={8760}
            value={form.interval_hours}
            onChange={(e) => setForm({ ...form, interval_hours: Number(e.target.value) || 1 })}
          />
        </div>
        <div className="form-group" style={{ marginBottom: 0 }}>
          <label>Window (days) <span className="hint">look-back per run</span></label>
          <input
            className="input"
            type="number"
            min={1}
            max={365}
            value={form.window_days}
            onChange={(e) => setForm({ ...form, window_days: Number(e.target.value) || 1 })}
          />
        </div>
      </div>

      <div className="form-group">
        <label>Tags to auto-fetch</label>
        {knownProducts.length > 0 ? (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {knownProducts.map((p) => (
              <label
                key={p}
                className="tag-chip"
                style={{
                  cursor: 'pointer',
                  background: form.products.includes(p) ? 'var(--primary)' : undefined,
                  color: form.products.includes(p) ? '#fff' : undefined,
                }}
              >
                <input
                  type="checkbox"
                  checked={form.products.includes(p)}
                  onChange={() => toggleProduct(p)}
                  style={{ display: 'none' }}
                />
                {p}
              </label>
            ))}
          </div>
        ) : (
          <div className="text-muted text-sm">
            No known tags yet — fetch a tag manually first on the Fetch page, then it'll show up here to schedule.
          </div>
        )}
      </div>

      {status && (
        <div className="flex items-center gap-12" style={{ flexWrap: 'wrap', marginBottom: 16, fontSize: 13 }}>
          {status.enabled ? (
            <span className="badge badge-green">Enabled</span>
          ) : (
            <span className="badge badge-gray">Disabled</span>
          )}
          {status.running && (
            <span className="badge badge-amber"><Loader size={10} className="spin" /> Running now</span>
          )}
          <span className="text-muted"><Clock size={11} style={{ verticalAlign: '-1px', marginRight: 3 }} />Last run: {fmtDate(status.last_run_at)}</span>
          <span className="text-muted"><Clock size={11} style={{ verticalAlign: '-1px', marginRight: 3 }} />Next run: {fmtDate(status.next_run_at)}</span>
        </div>
      )}

      {saveMsg && (
        <div className={`alert ${saveMsg.ok ? 'alert-success' : 'alert-error'}`} style={{ marginBottom: 0 }}>
          {saveMsg.ok ? <CheckCircle size={14} /> : <XCircle size={14} />}
          {saveMsg.text}
        </div>
      )}

      <div className="btn-group" style={{ marginTop: 20 }}>
        <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? <Loader size={14} className="spin" /> : null}
          Save schedule
        </button>
        <button className="btn btn-secondary" onClick={handleTrigger} disabled={triggering || !form.products.length}>
          {triggering ? <Loader size={14} className="spin" /> : <PlayCircle size={14} />}
          Run now
        </button>
      </div>
    </div>
  )
}

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

      <ScheduleSection />
    </>
  )
}
