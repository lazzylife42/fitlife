import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from './api'

const DAYS = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim']
const SCHEDULE = [
  { type: 'rest', label: 'Repos' },
  { type: 'gym', label: 'FB A', tag: 'push' },
  { type: 'run', label: 'Course Z2', sub: '3-5km' },
  { type: 'gym', label: 'FB B', tag: 'pull' },
  { type: 'active', label: 'Marche' },
  { type: 'gym', label: 'FB C', tag: 'mix' },
  { type: 'run', label: 'Course Z2', sub: '5-8km' },
]
const MUSCU = {
  'FB A': [
    { name: 'Leg Press', sets: '3×12', ref: '60-80kg', key: 'A_legpress' },
    { name: 'Chest Press', sets: '3×12', ref: '30-40kg', key: 'A_chestpress' },
    { name: 'Élév. latérales', sets: '3×15', ref: '5-8kg', key: 'A_lateral' },
    { name: 'Triceps câble', sets: '3×15', ref: '15-20kg', key: 'A_triceps' },
    { name: 'Crunch machine', sets: '3×15', ref: '—', key: 'A_crunch' },
  ],
  'FB B': [
    { name: 'Leg Curl', sets: '3×12', ref: '25-35kg', key: 'B_legcurl' },
    { name: 'Low Row', sets: '3×12', ref: '30-40kg', key: 'B_lowrow' },
    { name: 'Lat Pulldown', sets: '3×12', ref: '35-45kg', key: 'B_latpull' },
    { name: 'Curl biceps', sets: '3×15', ref: '15-20kg', key: 'B_curl' },
    { name: 'Adduct/Abduct', sets: '3×15', ref: '30-40kg', key: 'B_adduct' },
  ],
  'FB C': [
    { name: 'Leg Press', sets: '3×10', ref: '70-90kg', key: 'C_legpress' },
    { name: 'Pec Deck', sets: '3×15', ref: '25-35kg', key: 'C_pecdeck' },
    { name: 'Lat Pull serrée', sets: '3×12', ref: '35-40kg', key: 'C_latpull' },
    { name: 'Shoulder Press', sets: '3×12', ref: '20-30kg', key: 'C_shoulder' },
    { name: 'Planche', sets: '3×30-45s', ref: '—', key: 'C_planche' },
  ],
}

function getWeekNum() {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() + 3 - ((d.getDay() + 6) % 7))
  const w = new Date(d.getFullYear(), 0, 4)
  return Math.round(((d - w) / 86400000 - 3 + ((w.getDay() + 6) % 7)) / 7)
}

function getYear() { return new Date().getFullYear() }

const todayIdx = (new Date().getDay() + 6) % 7
const weekNum = getWeekNum()
const year = getYear()

export default function App() {
  const [state, setState] = useState(null)
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('week')
  const [activeMuscu, setActiveMuscu] = useState('FB A')
  const [stravaData, setStravaData] = useState(null)
  const [stravaConnected, setStravaConnected] = useState(false)
  const [googleConnected, setGoogleConnected] = useState(false)
  const [calendarCreating, setCalendarCreating] = useState(false)
  const [toast, setToast] = useState(null)
  const saveTimer = useRef(null)

  const showToast = (msg, type = 'ok') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 2500)
  }

  useEffect(() => {
    Promise.all([
      api.getState(),
      api.stravaStatus(),
      api.googleStatus(),
    ]).then(([s, strava, google]) => {
      setState(s)
      setStravaConnected(strava.connected)
      setGoogleConnected(google.connected)
      if (strava.connected) fetchStrava()
    }).catch(() => showToast('Erreur chargement', 'err'))
      .finally(() => setLoading(false))

    const params = new URLSearchParams(window.location.search)
    if (params.get('strava') === 'connected') showToast('Strava connecté !')
    if (params.get('google') === 'connected') showToast('Google Calendar connecté !')
    window.history.replaceState({}, '', '/')
  }, [])

  const fetchStrava = async () => {
    try {
      const data = await api.stravaActivities()
      setStravaData(data)
    } catch {}
  }

  const isDone = (dayIdx) => {
    if (!state?.sessions) return false
    return state.sessions.some(s => s.week_num === weekNum && s.year === year && s.day_index === dayIdx && s.done)
  }

  const toggleDay = async (i) => {
    if (SCHEDULE[i].type === 'rest') return
    const done = !isDone(i)
    setState(prev => {
      const sessions = (prev.sessions || []).filter(
        s => !(s.week_num === weekNum && s.year === year && s.day_index === i)
      )
      if (done) sessions.push({ week_num: weekNum, year, day_index: i, done: 1 })
      return { ...prev, sessions }
    })
    try {
      await api.toggleSession(weekNum, year, i, done)
    } catch { showToast('Erreur sync', 'err') }
  }

  const addLog = async (type, value) => {
    if (!value || isNaN(value)) return false
    setState(prev => ({ ...prev, [type]: parseFloat(value) }))
    try {
      await api.log(type, parseFloat(value))
      showToast('Enregistré')
      return true
    } catch {
      showToast('Erreur sync', 'err')
      return false
    }
  }

  const saveCharge = useCallback((key, val) => {
    setState(prev => ({ ...prev, charges: { ...(prev.charges || {}), [key]: val } }))
    clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      api.saveCharges({ [key]: val }).catch(() => {})
    }, 800)
  }, [])

  const connectStrava = async () => {
    const { url } = await api.stravaAuth()
    window.location.href = url
  }

  const connectGoogle = async () => {
    const { url } = await api.googleAuth()
    window.location.href = url
  }

  const createCalendarEvents = async () => {
    setCalendarCreating(true)
    try {
      const result = await api.createCalendarEvents({ start_time: '07:00', weeks: 12 })
      showToast(`${result.created} événements créés`)
    } catch {
      showToast('Erreur création', 'err')
    } finally {
      setCalendarCreating(false)
    }
  }

  if (loading) return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', color: 'var(--c-text-2)' }}>Chargement...</div>
  if (!state) return null

  const doneCount = SCHEDULE.filter((_, i) => isDone(i)).length
  const totalWorkouts = SCHEDULE.filter(s => s.type !== 'rest' && s.type !== 'active').length
  const kmWeek = stravaData?.total_km_week ?? (state.km || 0)
  const cigTarget = Math.max(25 - weekNum, 0)
  const cigs = state.cigs || 0
  const poids = state.poids || 85
  const fc = state.fc || null
  const poidsLogs = state.logs?.poids || []
  const poidsDelta = poidsLogs.length >= 2
    ? Math.round((poidsLogs[0].value - poidsLogs[1].value) * 10) / 10
    : null

  return (
    <div style={{ maxWidth: 480, margin: '0 auto', paddingBottom: 80 }}>
      {toast && (
        <div style={{
          position: 'fixed', top: 16, left: '50%', transform: 'translateX(-50%)',
          background: toast.type === 'err' ? 'var(--red)' : 'var(--green)',
          color: 'white', padding: '8px 20px', borderRadius: 20, fontSize: 13,
          zIndex: 100, whiteSpace: 'nowrap', fontWeight: 500,
        }}>{toast.msg}</div>
      )}

      <header style={{ padding: '1rem 1rem 0', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 500 }}>FitLife</div>
          <div style={{ fontSize: 12, color: 'var(--c-text-2)', marginTop: 2 }}>
            {new Date().toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' })}
          </div>
        </div>
        <div style={{ fontSize: 13, color: 'var(--c-text-2)' }}>{doneCount}/{totalWorkouts}</div>
      </header>

      <nav style={{ display: 'flex', gap: 4, padding: '1rem 1rem 0', overflowX: 'auto' }}>
        {[['week', 'Semaine'], ['metrics', 'Métriques'], ['muscu', 'Charges'], ['connect', 'Intégrations']].map(([id, label]) => (
          <button key={id} onClick={() => setActiveTab(id)} style={{
            padding: '6px 14px', borderRadius: 20, fontSize: 13, cursor: 'pointer', whiteSpace: 'nowrap',
            border: '0.5px solid var(--c-border)',
            background: activeTab === id ? 'var(--c-text)' : 'transparent',
            color: activeTab === id ? 'var(--c-bg)' : 'var(--c-text-2)',
            flexShrink: 0,
          }}>{label}</button>
        ))}
      </nav>

      <main style={{ padding: '1rem' }}>

        {/* --- Semaine --- */}
        {activeTab === 'week' && (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 6, marginBottom: '1rem' }}>
              {SCHEDULE.map((s, i) => {
                const done = isDone(i)
                const isToday = i === todayIdx
                const clickable = s.type !== 'rest' && s.type !== 'active'
                return (
                  <div key={i} onClick={() => toggleDay(i)} style={{
                    borderRadius: 8,
                    border: isToday ? '1.5px solid var(--blue)' : '0.5px solid var(--c-border)',
                    padding: '8px 4px', textAlign: 'center',
                    cursor: clickable ? 'pointer' : 'default',
                    background: done ? 'var(--green-light)' : s.type === 'rest' ? 'var(--c-bg-2)' : 'var(--c-bg)',
                  }}>
                    <div style={{ fontSize: 10, fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.04em', color: done ? 'var(--green-dark)' : 'var(--c-text-2)', marginBottom: 3 }}>{DAYS[i]}</div>
                    <div style={{ fontSize: 9, color: done ? 'var(--green)' : 'var(--c-text-2)', lineHeight: 1.4 }}>
                      {s.label}
                      {s.sub && <><br /><span style={{ color: 'var(--c-text-3)', fontSize: 8 }}>{s.sub}</span></>}
                    </div>
                    {clickable && (
                      <div style={{
                        width: 18, height: 18, borderRadius: '50%', margin: '4px auto 0',
                        border: done ? 'none' : '1px solid var(--c-border-med)',
                        background: done ? 'var(--green)' : 'transparent',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                      }}>
                        {done && <svg width="9" height="9" viewBox="0 0 12 12" fill="none"><polyline points="1.5,6 4.5,9 10.5,3" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>

            <Card title="Zones cardio">
              <div style={{ display: 'flex', borderRadius: 6, overflow: 'hidden', height: 20, marginBottom: 8 }}>
                {[['#B5D4F4', 1], ['#C0DD97', 2.2], ['#FAC775', 1.2], ['#F5C4B3', 1.2], ['#F7C1C1', 0.8]].map(([bg, flex], i) => (
                  <div key={i} style={{ flex, background: bg }} />
                ))}
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 8 }}>
                {[['Z1','<110','#B5D4F4','#0C447C'],['Z2','110-140 cible','#C0DD97','#3B6D11'],['Z3','140-155','#FAC775','#633806'],['Z4','155-172','#F5C4B3','#712B13'],['Z5','>172','#F7C1C1','#791F1F']].map(([z,r,bg,c]) => (
                  <span key={z} style={{ fontSize: 10, padding: '2px 7px', borderRadius: 20, background: bg, color: c, fontWeight: 500 }}>{z} {r}</span>
                ))}
              </div>
              <Row label="PR 5km" value="34:31" badge="6:51/km" />
              <Row label="Km cette semaine" value={`${kmWeek} / 10 km`} />
              {stravaData && (
                <div style={{ marginTop: 8 }}>
                  {stravaData.activities.slice(0, 5).map(a => (
                    <div key={a.id} style={{ fontSize: 12, padding: '5px 0', borderTop: '0.5px solid var(--c-border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span style={{ display: 'flex', alignItems: 'center', gap: 5, color: 'var(--c-text-2)' }}>
                        <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 20, background: a.type === 'Run' ? 'var(--teal-light)' : 'var(--c-bg-2)', color: a.type === 'Run' ? 'var(--teal)' : 'var(--c-text-3)', fontWeight: 500 }}>{a.type === 'Run' ? 'Course' : 'Marche'}</span>
                        {new Date(a.date).toLocaleDateString('fr-FR', { weekday: 'short', day: 'numeric' })} — {a.name}
                      </span>
                      <span style={{ fontWeight: 500, color: 'var(--c-text)', whiteSpace: 'nowrap', marginLeft: 8 }}>{a.distance_km}km {a.avg_hr ? `· ${Math.round(a.avg_hr)}bpm` : ''}</span>
                    </div>
                  ))}
                </div>
              )}
              <LogInput placeholder="km aujourd'hui" onLog={v => addLog('km', v)} unit="km" />
            </Card>
          </>
        )}

        {/* --- Métriques --- */}
        {activeTab === 'metrics' && (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 12 }}>
              <MetricCard label="Poids" value={poids} unit="kg"
                delta={poidsDelta !== null ? `${poidsDelta > 0 ? '+' : ''}${poidsDelta} kg` : '—'}
                deltaColor={poidsDelta === null ? 'var(--c-text-3)' : poidsDelta < 0 ? 'var(--teal)' : 'var(--red)'} />
              <MetricCard label="FC repos" value={fc || '—'} unit={fc ? 'bpm' : ''}
                delta={!fc ? 'matin à jeun' : fc < 60 ? 'Excellent' : fc < 70 ? 'Bon' : fc < 80 ? 'Normal' : 'Élevé'}
                deltaColor={!fc ? 'var(--c-text-3)' : fc < 60 ? 'var(--teal)' : fc < 70 ? 'var(--green)' : fc < 80 ? 'var(--amber)' : 'var(--red)'} />
              <MetricCard label="Km semaine" value={kmWeek} unit="km"
                delta={<ProgressBar pct={Math.min(kmWeek / 10 * 100, 100)} color="var(--teal)" />} />
              <MetricCard label="Cig/jour" value={cigs} unit="/j"
                delta={`max ${cigTarget}/j`} deltaColor="var(--amber)" />
            </div>

            <Card title="Tabac">
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 10 }}>
                {Array.from({ length: 25 }, (_, i) => (
                  <div key={i} onClick={() => {
                    const next = i < cigs ? i : i + 1
                    addLog('cigs', next)
                  }} style={{
                    width: 20, height: 20, borderRadius: 3, cursor: 'pointer',
                    background: i < cigs ? 'var(--amber)' : 'var(--c-border)',
                    transition: 'background 0.1s',
                  }} />
                ))}
              </div>
              <ProgressBar pct={Math.min(cigs / cigTarget * 100, 100)}
                color={cigs >= cigTarget ? 'var(--red)' : cigs >= cigTarget * 0.8 ? 'var(--amber)' : 'var(--green)'} />
            </Card>

            <div style={{ height: 12 }} />

            <Card title="Log métriques">
              <LogInput placeholder="Poids (kg)" onLog={v => addLog('poids', v)} unit="kg" step="0.1" />
              <div style={{ height: 8 }} />
              <LogInput placeholder="FC repos (bpm)" onLog={v => addLog('fc', v)} unit="bpm" />
            </Card>

            {poidsLogs.length > 0 && (
              <>
                <div style={{ height: 12 }} />
                <Card title="Historique poids">
                  {poidsLogs.slice(0, 7).map((l, i) => (
                    <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', borderBottom: i < Math.min(poidsLogs.length, 7) - 1 ? '0.5px solid var(--c-border)' : 'none', fontSize: 13 }}>
                      <span style={{ color: 'var(--c-text-2)' }}>{new Date(l.date).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })}</span>
                      <span style={{ fontWeight: 500 }}>{l.value} kg</span>
                    </div>
                  ))}
                </Card>
              </>
            )}
          </>
        )}

        {/* --- Charges --- */}
        {activeTab === 'muscu' && (
          <>
            <div style={{ display: 'flex', gap: 4, marginBottom: 12, overflowX: 'auto' }}>
              {Object.keys(MUSCU).map(k => (
                <button key={k} onClick={() => setActiveMuscu(k)} style={{
                  padding: '6px 14px', borderRadius: 20, fontSize: 13, cursor: 'pointer', whiteSpace: 'nowrap',
                  border: '0.5px solid var(--c-border)', flexShrink: 0,
                  background: activeMuscu === k ? 'var(--c-text)' : 'transparent',
                  color: activeMuscu === k ? 'var(--c-bg)' : 'var(--c-text-2)',
                }}>{k}</button>
              ))}
            </div>
            <Card>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr>{['Exercice', 'Séries', 'Réf.', 'Charge'].map(h => (
                    <th key={h} style={{ fontSize: 10, fontWeight: 500, color: 'var(--c-text-3)', textAlign: 'left', padding: '0 0 8px', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{h}</th>
                  ))}</tr>
                </thead>
                <tbody>
                  {MUSCU[activeMuscu].map((ex, i) => (
                    <tr key={ex.key}>
                      <td style={{ padding: '7px 4px 7px 0', borderBottom: i < MUSCU[activeMuscu].length - 1 ? '0.5px solid var(--c-border)' : 'none', fontWeight: 500, fontSize: 12 }}>{ex.name}</td>
                      <td style={{ padding: '7px 4px', borderBottom: i < MUSCU[activeMuscu].length - 1 ? '0.5px solid var(--c-border)' : 'none', color: 'var(--c-text-2)', fontSize: 11 }}>{ex.sets}</td>
                      <td style={{ padding: '7px 4px', borderBottom: i < MUSCU[activeMuscu].length - 1 ? '0.5px solid var(--c-border)' : 'none', color: 'var(--c-text-3)', fontSize: 11 }}>{ex.ref}</td>
                      <td style={{ padding: '7px 0', borderBottom: i < MUSCU[activeMuscu].length - 1 ? '0.5px solid var(--c-border)' : 'none' }}>
                        <input type="text" defaultValue={state.charges?.[ex.key] || ''}
                          placeholder="kg"
                          onBlur={e => saveCharge(ex.key, e.target.value)}
                          style={{ width: 60, padding: '4px 6px', borderRadius: 6, border: '0.5px solid var(--c-border-med)', background: 'var(--c-bg-2)', color: 'var(--c-text)', fontSize: 13, textAlign: 'center' }} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          </>
        )}

        {/* --- Intégrations --- */}
        {activeTab === 'connect' && (
          <>
            <Card title="Strava">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>Strava</div>
                  <div style={{ fontSize: 12, color: 'var(--c-text-2)' }}>Import courses automatique</div>
                </div>
                <StatusDot connected={stravaConnected} />
              </div>
              {!stravaConnected ? (
                <button onClick={connectStrava} style={connectBtnStyle}>Connecter Strava ↗</button>
              ) : (
                <button onClick={fetchStrava} style={connectBtnStyle}>Rafraîchir les données</button>
              )}
            </Card>

            <div style={{ height: 12 }} />

            <Card title="Google Calendar">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>Google Calendar</div>
                  <div style={{ fontSize: 12, color: 'var(--c-text-2)' }}>Rappels séances 12 semaines</div>
                </div>
                <StatusDot connected={googleConnected} />
              </div>
              {!googleConnected ? (
                <button onClick={connectGoogle} style={connectBtnStyle}>Connecter Google ↗</button>
              ) : (
                <button onClick={createCalendarEvents} disabled={calendarCreating} style={connectBtnStyle}>
                  {calendarCreating ? 'Création...' : 'Créer les événements ↗'}
                </button>
              )}
            </Card>
          </>
        )}

      </main>
    </div>
  )
}

function Card({ title, children }) {
  return (
    <div style={{ background: 'var(--c-bg)', border: '0.5px solid var(--c-border)', borderRadius: 12, padding: '1rem' }}>
      {title && <div style={{ fontSize: 11, fontWeight: 500, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--c-text-3)', marginBottom: 10 }}>{title}</div>}
      {children}
    </div>
  )
}

function MetricCard({ label, value, unit, delta, deltaColor }) {
  return (
    <div style={{ background: 'var(--c-bg-2)', borderRadius: 8, padding: '0.875rem' }}>
      <div style={{ fontSize: 12, color: 'var(--c-text-2)', marginBottom: 4 }}>{label}</div>
      <div><span style={{ fontSize: 24, fontWeight: 500 }}>{value}</span><span style={{ fontSize: 12, color: 'var(--c-text-2)', marginLeft: 2 }}>{unit}</span></div>
      <div style={{ fontSize: 11, marginTop: 4, color: deltaColor || 'var(--c-text-3)' }}>{delta}</div>
    </div>
  )
}

function ProgressBar({ pct, color }) {
  return (
    <div style={{ background: 'var(--c-bg-2)', borderRadius: 20, height: 5, overflow: 'hidden' }}>
      <div style={{ width: `${pct}%`, height: '100%', borderRadius: 20, background: color, transition: 'width 0.4s' }} />
    </div>
  )
}

function Row({ label, value, badge }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0', borderBottom: '0.5px solid var(--c-border)' }}>
      <span style={{ fontSize: 12, color: 'var(--c-text-2)', flex: 1 }}>{label}</span>
      <span style={{ fontSize: 13, fontWeight: 500 }}>{value}</span>
      {badge && <span style={{ fontSize: 10, padding: '2px 7px', borderRadius: 20, background: 'var(--purple-light)', color: 'var(--purple-dark)' }}>{badge}</span>}
    </div>
  )
}

function StatusDot({ connected }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: connected ? 'var(--green)' : 'var(--c-text-3)' }}>
      <div style={{ width: 8, height: 8, borderRadius: '50%', background: connected ? 'var(--green)' : 'var(--c-border-med)' }} />
      {connected ? 'Connecté' : 'Non connecté'}
    </div>
  )
}

function LogInput({ placeholder, onLog, unit, step = '1' }) {
  const [val, setVal] = useState('')
  const submit = async () => {
    const ok = await onLog(val)
    if (ok) setVal('')
  }
  return (
    <div style={{ display: 'flex', gap: 8 }}>
      <input type="number" value={val} onChange={e => setVal(e.target.value)} placeholder={placeholder} step={step}
        onKeyDown={e => e.key === 'Enter' && submit()}
        style={{ flex: 1, padding: '8px 12px', borderRadius: 8, border: '0.5px solid var(--c-border-med)', background: 'var(--c-bg-2)', color: 'var(--c-text)', fontSize: 14 }} />
      <button onClick={submit} style={{ padding: '8px 16px', borderRadius: 8, border: '0.5px solid var(--c-border-med)', background: 'transparent', color: 'var(--c-text)', fontSize: 13, cursor: 'pointer' }}>
        + {unit}
      </button>
    </div>
  )
}

const connectBtnStyle = {
  width: '100%', padding: '10px', borderRadius: 8,
  border: '0.5px solid var(--c-border-med)', background: 'transparent',
  color: 'var(--c-text)', fontSize: 13, cursor: 'pointer', textAlign: 'center',
}
