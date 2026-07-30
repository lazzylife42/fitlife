import { useState, useEffect, useCallback } from 'react'
import { api, getToken, setToken, clearToken } from './api'

// ============ Root ============

export default function App() {
  const [me, setMe] = useState(null)
  const [loading, setLoading] = useState(true)
  const [toast, setToast] = useState(null)

  const showToast = useCallback((msg, type = 'ok') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 2500)
  }, [])

  const refreshMe = useCallback(async () => {
    try { setMe(await api.me()) } catch { setMe(null) }
  }, [])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('strava') === 'connected') {
      showToast('Strava connecté !')
      window.history.replaceState({}, '', '/')
    }
    if (!getToken()) { setLoading(false); return }
    refreshMe().finally(() => setLoading(false))
  }, [refreshMe, showToast])

  if (loading) return <Center>Chargement...</Center>

  return (
    <div style={{ maxWidth: 480, margin: '0 auto', paddingBottom: 40 }}>
      {toast && (
        <div style={{
          position: 'fixed', top: 16, left: '50%', transform: 'translateX(-50%)',
          background: toast.type === 'err' ? 'var(--red)' : 'var(--green)',
          color: 'white', padding: '8px 20px', borderRadius: 20, fontSize: 13,
          zIndex: 100, whiteSpace: 'nowrap', fontWeight: 500,
        }}>{toast.msg}</div>
      )}
      {!me
        ? <AuthScreen onAuthed={refreshMe} showToast={showToast} />
        : !me.profile
          ? <Onboarding onDone={refreshMe} showToast={showToast} />
          : <Main me={me} refreshMe={refreshMe} showToast={showToast} />}
    </div>
  )
}

// ============ Labels FR (dataset en anglais) ============

const CAT_FR = {
  'upper legs': 'Jambes', 'lower legs': 'Mollets', 'upper arms': 'Bras',
  'lower arms': 'Avant-bras', 'back': 'Dos', 'chest': 'Pectoraux',
  'shoulders': 'Épaules', 'waist': 'Abdos', 'cardio': 'Cardio', 'neck': 'Cou',
}
const EQUIP_FR = {
  'leverage machine': 'Machine guidée', 'cable': 'Câble', 'smith machine': 'Smith machine',
  'dumbbell': 'Haltères', 'body weight': 'Poids du corps',
}
const catFr = c => CAT_FR[c] || c
const equipFr = e => EQUIP_FR[e] || e
const cap = s => s ? s.charAt(0).toUpperCase() + s.slice(1) : s

// ============ Auth ============

function AuthScreen({ onAuthed, showToast }) {
  const [mode, setMode] = useState('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setBusy(true)
    try {
      const fn = mode === 'login' ? api.login : api.register
      const { token } = await fn(email, password)
      setToken(token)
      await onAuthed()
    } catch (e) {
      showToast(e.message, 'err')
    } finally { setBusy(false) }
  }

  return (
    <Center>
      <div style={{ width: '100%', padding: '0 1.5rem' }}>
        <div style={{ fontSize: 28, fontWeight: 500, textAlign: 'center', marginBottom: 4 }}>FitLife</div>
        <div style={{ fontSize: 13, color: 'var(--c-text-2)', textAlign: 'center', marginBottom: 24 }}>
          {mode === 'login' ? 'Connexion' : 'Créer un compte'}
        </div>
        <input type="email" placeholder="Email" value={email} onChange={e => setEmail(e.target.value)}
          style={inputStyle} autoComplete="email" />
        <div style={{ height: 8 }} />
        <input type="password" placeholder="Mot de passe (8 min)" value={password}
          onChange={e => setPassword(e.target.value)} style={inputStyle}
          onKeyDown={e => e.key === 'Enter' && submit()}
          autoComplete={mode === 'login' ? 'current-password' : 'new-password'} />
        <div style={{ height: 16 }} />
        <button onClick={submit} disabled={busy} style={primaryBtnStyle}>
          {busy ? '...' : mode === 'login' ? 'Se connecter' : 'Créer le compte'}
        </button>
        <div style={{ height: 12 }} />
        <button onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
          style={{ ...ghostBtnStyle, width: '100%' }}>
          {mode === 'login' ? 'Pas de compte ? S\'inscrire' : 'Déjà un compte ? Se connecter'}
        </button>
      </div>
    </Center>
  )
}

// ============ Onboarding QCM ============

const QCM = [
  { key: 'goal', label: 'Ton objectif principal ?', options: [
    ['recomp', 'Recomposition (perte gras + muscle)'],
    ['strength', 'Force'],
    ['endurance', 'Endurance']] },
  { key: 'gym_days', label: 'Séances salle par semaine ?', options: [[2, '2'], [3, '3'], [4, '4']] },
  { key: 'focus', label: 'Focus ?', options: [
    ['balanced', 'Équilibré'], ['upper', 'Haut du corps'], ['lower', 'Bas du corps']] },
  { key: 'level', label: 'Ton niveau ?', options: [
    ['beginner', 'Débutant'], ['intermediate', 'Intermédiaire']] },
  { key: 'equipment_pref', label: 'Équipement ?', options: [
    ['machines', 'Machines guidées only'],
    ['machines_dumbbells', 'Machines + haltères'],
    ['all', 'Tout']] },
  { key: 'run_days', label: 'Jours de course par semaine ?', options: [
    [0, 'Pas de course'], [1, '1'], [2, '2'], [3, '3']] },
  { key: 'run_km_target', label: 'Km de course par semaine (objectif) ?', options: [
    [0, '0'], [5, '5'], [10, '10'], [15, '15'], [20, '20']], showIf: p => p.run_days > 0 },
]

function Onboarding({ onDone, showToast, initial = {}, onCancel }) {
  const [answers, setAnswers] = useState(initial)
  const [busy, setBusy] = useState(false)
  const visible = QCM.filter(q => !q.showIf || q.showIf(answers))
  const complete = visible.every(q => answers[q.key] !== undefined)

  const submit = async () => {
    setBusy(true)
    try {
      await api.saveProfile({ run_km_target: 0, ...answers })
      await api.generateWorkout()
      await onDone()
    } catch (e) { showToast(e.message, 'err') } finally { setBusy(false) }
  }

  return (
    <div style={{ padding: '2rem 1rem 1rem' }}>
      <div style={{ fontSize: 20, fontWeight: 500, marginBottom: 4 }}>Ton programme</div>
      <div style={{ fontSize: 13, color: 'var(--c-text-2)', marginBottom: 20 }}>
        5 questions, l'app génère tes séances.
      </div>
      {visible.map(q => (
        <div key={q.key} style={{ marginBottom: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8 }}>{q.label}</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {q.options.map(([val, label]) => (
              <button key={String(val)} onClick={() => setAnswers(a => ({ ...a, [q.key]: val }))}
                style={{
                  padding: '8px 14px', borderRadius: 20, fontSize: 13, cursor: 'pointer',
                  border: '0.5px solid var(--c-border-med)',
                  background: answers[q.key] === val ? 'var(--c-text)' : 'transparent',
                  color: answers[q.key] === val ? 'var(--c-bg)' : 'var(--c-text-2)',
                }}>{label}</button>
            ))}
          </div>
        </div>
      ))}
      <button onClick={submit} disabled={!complete || busy} style={{
        ...primaryBtnStyle, opacity: complete ? 1 : 0.4,
      }}>{busy ? 'Génération...' : 'Générer mon programme'}</button>
      {onCancel && (
        <button onClick={onCancel} style={{ ...ghostBtnStyle, width: '100%', marginTop: 8 }}>Annuler</button>
      )}
    </div>
  )
}

// ============ Main (tabs) ============

function Main({ me, refreshMe, showToast }) {
  const [tab, setTab] = useState('seance')
  return (
    <>
      <header style={{ padding: '1rem 1rem 0', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 500 }}>FitLife</div>
          <div style={{ fontSize: 12, color: 'var(--c-text-2)', marginTop: 2 }}>
            {new Date().toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' })}
          </div>
        </div>
        <div style={{ fontSize: 12, color: 'var(--c-text-3)' }}>{me.user.email.split('@')[0]}</div>
      </header>
      <nav style={{ display: 'flex', gap: 4, padding: '1rem 1rem 0', overflowX: 'auto' }}>
        {[['seance', 'Séance'], ['exos', 'Exos'], ['metrics', 'Métriques'], ['profil', 'Profil']].map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)} style={{
            padding: '6px 14px', borderRadius: 20, fontSize: 13, cursor: 'pointer', whiteSpace: 'nowrap',
            border: '0.5px solid var(--c-border)', flexShrink: 0,
            background: tab === id ? 'var(--c-text)' : 'transparent',
            color: tab === id ? 'var(--c-bg)' : 'var(--c-text-2)',
          }}>{label}</button>
        ))}
      </nav>
      <main style={{ padding: '1rem' }}>
        {tab === 'seance' && <WorkoutTab me={me} showToast={showToast} />}
        {tab === 'exos' && <ExercisesTab />}
        {tab === 'metrics' && <MetricsTab showToast={showToast} />}
        {tab === 'profil' && <ProfileTab me={me} refreshMe={refreshMe} showToast={showToast} />}
      </main>
    </>
  )
}

// ============ Séance ============

function WorkoutTab({ me, showToast }) {
  const [data, setData] = useState(null)
  const [strava, setStrava] = useState(null)
  const [progress, setProgress] = useState(null)
  const [busy, setBusy] = useState(false)
  const [openEx, setOpenEx] = useState(null)
  const [sessionNote, setSessionNote] = useState('')

  const load = useCallback(async () => {
    if (me.strava_connected) {
      try { setStrava(await api.stravaActivities()) } catch {}
    }
    setData(await api.workouts())
    try { setProgress(await api.progress()) } catch {}
  }, [me.strava_connected])

  useEffect(() => { load() }, [load])

  if (!data) return <Center small>Chargement...</Center>
  const w = data.next

  const saveSet = async (setId, patch) => {
    setData(prev => ({
      ...prev,
      next: {
        ...prev.next,
        sets: prev.next.sets.map(s => s.id === setId
          ? { ...s, actual_weight: patch.actual_weight, done: patch.done ? 1 : 0,
              note: patch.note !== undefined ? patch.note : s.note }
          : s),
      },
    }))
    try { await api.updateSet(w.id, setId, patch) } catch { showToast('Erreur sync', 'err') }
  }

  const complete = async () => {
    setBusy(true)
    try {
      await api.completeWorkout(w.id, { note: sessionNote })
      setSessionNote('')
      showToast('Séance enregistrée, prochaine séance générée')
      await load()
    } catch (e) { showToast(e.message, 'err') } finally { setBusy(false) }
  }

  const switchMode = async (mode) => {
    setBusy(true)
    try {
      await api.setMode(mode)
      showToast(mode === 'travel' ? 'Mode sans matériel activé' : 'Retour mode salle')
      await load()
    } catch (e) { showToast(e.message, 'err') } finally { setBusy(false) }
  }

  const isTravel = w?.plan?.mode === 'travel'
  const doneSets = w ? w.sets.filter(s => s.done).length : 0

  return (
    <>
      {data.ai_pending && (
        <div style={{ fontSize: 12, color: 'var(--amber)', background: 'var(--amber-light)', padding: '8px 12px', borderRadius: 8, marginBottom: 12 }}>
          ⏳ Le coach IA affine ta prochaine séance (serveur en attente). Plan de secours affiché en attendant.
        </div>
      )}

      {w ? (
        <Card>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
            <div style={{ fontSize: 15, fontWeight: 600 }}>{w.plan.title}</div>
            <span style={{ fontSize: 10, padding: '2px 8px', borderRadius: 20, fontWeight: 500,
              background: w.source === 'ai' ? 'var(--teal-light)' : 'var(--c-bg-2)',
              color: w.source === 'ai' ? 'var(--teal)' : 'var(--c-text-3)' }}>
              {w.source === 'ai' ? 'coach IA' : 'standard'}
            </span>
          </div>
          <div style={{ fontSize: 11, color: 'var(--c-text-3)', marginBottom: 8 }}>{doneSets}/{w.sets.length} exercices</div>
          <div style={{ background: 'var(--c-bg-2)', borderRadius: 20, height: 4, overflow: 'hidden', marginBottom: 12 }}>
            <div style={{ width: `${w.sets.length ? doneSets / w.sets.length * 100 : 0}%`, height: '100%', background: 'var(--teal)', transition: 'width 0.3s' }} />
          </div>
          {w.plan.advice && (
            <div style={{ fontSize: 12, color: 'var(--c-text-2)', lineHeight: 1.5, background: 'var(--c-bg-2)', borderRadius: 8, padding: '8px 10px', marginBottom: 10 }}>
              {w.plan.advice}
            </div>
          )}
          {w.sets.map(s => (
            <SetRow key={s.id} s={s} onSave={saveSet} open={openEx === s.id} onToggleOpen={() => setOpenEx(openEx === s.id ? null : s.id)} />
          ))}
          <textarea value={sessionNote} onChange={e => setSessionNote(e.target.value)}
            placeholder="Note sur la séance (optionnel)"
            rows={2}
            style={{ width: '100%', marginTop: 4, padding: '6px 8px', borderRadius: 6, border: '0.5px solid var(--c-border-med)', background: 'var(--c-bg-2)', color: 'var(--c-text)', fontSize: 12, resize: 'vertical', fontFamily: 'inherit', boxSizing: 'border-box' }} />
          <div style={{ height: 8 }} />
          <button onClick={complete} disabled={busy} style={{ ...primaryBtnStyle, background: 'var(--teal)', color: 'white' }}>
            {busy ? '...' : 'Terminer la séance ✓'}
          </button>
          <div style={{ height: 8 }} />
          <button onClick={() => switchMode(isTravel ? 'gym' : 'travel')} disabled={busy} style={ghostBtnStyle}>
            {isTravel ? 'Repasser en mode salle' : 'Pas de salle aujourd\'hui ? Mode sans matériel'}
          </button>
        </Card>
      ) : (
        <Card><div style={{ fontSize: 13, color: 'var(--c-text-2)' }}>Aucune séance planifiée.</div></Card>
      )}

      {progress && (progress.runs.length > 0 || progress.gym_sessions.length > 0) && (
        <>
          <div style={{ height: 12 }} />
          <ProgressCard progress={progress} />
        </>
      )}

      {data.runs.length > 0 && (
        <>
          <div style={{ height: 12 }} />
          <Card title="Courses de la semaine">
            {data.runs.map(r => <RunRow key={r.id} r={r} onDone={load} showToast={showToast} />)}
            <div style={{ fontSize: 11, color: 'var(--c-text-3)', marginTop: 8 }}>
              À valider manuellement une fois la course faite.
            </div>
            {w?.plan?.run_advice && <div style={{ fontSize: 12, color: 'var(--c-text-2)', marginTop: 4 }}>{w.plan.run_advice}</div>}
          </Card>
        </>
      )}

      {strava && (
        <>
          <div style={{ height: 12 }} />
          <Card title={`Strava — ${strava.total_km_week} km cette semaine`}>
            {strava.activities.slice(0, 6).map(a => (
              <div key={a.id} style={{ fontSize: 12, padding: '5px 0', borderTop: '0.5px solid var(--c-border)', display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--c-text-2)' }}>
                  <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 20, background: a.type === 'Run' ? 'var(--teal-light)' : 'var(--c-bg-2)', color: a.type === 'Run' ? 'var(--teal)' : 'var(--c-text-3)', fontWeight: 500, marginRight: 5 }}>{a.type}</span>
                  {new Date(a.date).toLocaleDateString('fr-FR', { weekday: 'short', day: 'numeric' })}
                </span>
                <span style={{ fontWeight: 500 }}>{a.distance_km}km {a.avg_hr ? `· ${Math.round(a.avg_hr)}bpm` : ''}</span>
              </div>
            ))}
          </Card>
        </>
      )}

      {data.history.length > 0 && (
        <>
          <div style={{ height: 12 }} />
          <Card title="Historique">
            {data.history.map(h => (
              <div key={h.id} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '5px 0', borderBottom: '0.5px solid var(--c-border)' }}>
                <span style={{ color: 'var(--c-text-2)' }}>{h.plan.title}</span>
                <span style={{ color: 'var(--c-text-3)' }}>{new Date(h.completed_at).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })}</span>
              </div>
            ))}
          </Card>
        </>
      )}
    </>
  )
}

function SetRow({ s, onSave, open, onToggleOpen }) {
  const [weight, setWeight] = useState(s.actual_weight ?? s.target_weight ?? '')
  const [done, setDone] = useState(!!s.done)
  const [note, setNote] = useState(s.note ?? '')
  const bodyweight = s.equipment === 'body weight'

  const toggleDone = () => {
    const next = !done
    setDone(next)
    onSave(s.id, { actual_weight: bodyweight || weight === '' ? null : parseFloat(weight), done: next, note })
  }
  const blurWeight = () => {
    onSave(s.id, { actual_weight: weight === '' ? null : parseFloat(weight), done, note })
  }
  const blurNote = () => {
    onSave(s.id, { actual_weight: bodyweight || weight === '' ? null : parseFloat(weight), done, note })
  }

  return (
    <div style={{ borderBottom: '0.5px solid var(--c-border)', padding: '8px 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div onClick={onToggleOpen} style={{ flex: 1, cursor: 'pointer' }}>
          <div style={{ fontSize: 13, fontWeight: 500 }}>{cap(s.name)}</div>
          <div style={{ fontSize: 11, color: 'var(--c-text-3)' }}>
            {s.target_sets}×{s.target_reps}
            {bodyweight ? ' · poids du corps' : s.target_weight ? ` · cible ${s.target_weight}kg` : ''}
          </div>
        </div>
        {!bodyweight && (
          <input type="number" step="0.5" value={weight} placeholder="kg"
            onChange={e => setWeight(e.target.value)} onBlur={blurWeight}
            style={{ width: 60, padding: '5px 6px', borderRadius: 6, border: '0.5px solid var(--c-border-med)', background: 'var(--c-bg-2)', color: 'var(--c-text)', fontSize: 13, textAlign: 'center' }} />
        )}
        <div onClick={toggleDone} style={{
          width: 24, height: 24, borderRadius: '50%', cursor: 'pointer', flexShrink: 0,
          border: done ? 'none' : '1px solid var(--c-border-med)',
          background: done ? 'var(--green)' : 'transparent',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          {done && <svg width="11" height="11" viewBox="0 0 12 12" fill="none"><polyline points="1.5,6 4.5,9 10.5,3" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>}
        </div>
      </div>
      {open && (
        <div style={{ marginTop: 8 }}>
          {s.gif && <img src={s.gif} alt="" onError={e => { e.target.style.display = 'none' }} style={{ width: '100%', borderRadius: 8, marginBottom: 6 }} loading="lazy" />}
          <InstructionsFR exerciseId={s.exercise_id} fallback={s.instructions} />
          <textarea value={note} onChange={e => setNote(e.target.value)} onBlur={blurNote}
            placeholder="Note (ex: fait aux haltères, pas de machine dispo)"
            rows={2}
            style={{ width: '100%', marginTop: 8, padding: '6px 8px', borderRadius: 6, border: '0.5px solid var(--c-border-med)', background: 'var(--c-bg-2)', color: 'var(--c-text)', fontSize: 12, resize: 'vertical', fontFamily: 'inherit', boxSizing: 'border-box' }} />
        </div>
      )}
    </div>
  )
}

function weeklyBuckets(dates, weeks = 6) {
  const now = new Date()
  const buckets = Array.from({ length: weeks }, () => 0)
  dates.forEach(({ date, value }) => {
    const daysAgo = Math.floor((now - new Date(date)) / 86400000)
    const idx = weeks - 1 - Math.floor(daysAgo / 7)
    if (idx >= 0 && idx < weeks) buckets[idx] += (value ?? 1)
  })
  return buckets.map((v, i) => ({
    label: i === weeks - 1 ? 'cette sem.' : `S-${weeks - 1 - i}`,
    value: Math.round(v * 10) / 10,
  }))
}

function ProgressCard({ progress }) {
  const kmBars = weeklyBuckets(progress.runs.map(r => ({ date: r.date, value: r.km || 0 })))
  const gymBars = weeklyBuckets(progress.gym_sessions.map(s => ({ date: s.date, value: 1 })))
  const totalKm = kmBars.reduce((s, b) => s + b.value, 0)
  const totalGym = gymBars.reduce((s, b) => s + b.value, 0)
  return (
    <Card title="Progression — 6 dernières semaines">
      {totalKm > 0 && (
        <>
          <div style={{ fontSize: 12, color: 'var(--c-text-2)', marginBottom: 6 }}>Course ({totalKm.toFixed(1)} km au total)</div>
          <BarChart bars={kmBars} unit="km" color="var(--teal)" />
          <div style={{ height: 14 }} />
        </>
      )}
      {totalGym > 0 && (
        <>
          <div style={{ fontSize: 12, color: 'var(--c-text-2)', marginBottom: 6 }}>Séances salle ({totalGym})</div>
          <BarChart bars={gymBars} unit="séances" color="var(--amber)" />
        </>
      )}
    </Card>
  )
}

function RunRow({ r, onDone, showToast }) {
  const [open, setOpen] = useState(false)
  const [km, setKm] = useState(r.plan.km || '')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  const validate = async () => {
    setBusy(true)
    try {
      await api.completeWorkout(r.id, { distance_km: km === '' ? null : parseFloat(km), note })
      showToast('Course validée')
      await onDone()
    } catch (e) { showToast(e.message, 'err') } finally { setBusy(false) }
  }

  return (
    <div style={{ padding: '6px 0', borderBottom: '0.5px solid var(--c-border)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 500 }}>{r.plan.title} — {r.plan.km} km</div>
          <div style={{ fontSize: 11, color: 'var(--c-text-3)' }}>{r.plan.zone}</div>
        </div>
        <button onClick={() => setOpen(!open)} style={{ ...ghostBtnStyle, padding: '4px 10px', fontSize: 11 }}>
          {open ? 'Annuler' : 'Marquer fait'}
        </button>
      </div>
      {open && (
        <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <input type="number" step="0.1" value={km} onChange={e => setKm(e.target.value)}
            placeholder="Distance parcourue (km)"
            style={{ width: '100%', padding: '6px 8px', borderRadius: 6, border: '0.5px solid var(--c-border-med)', background: 'var(--c-bg-2)', color: 'var(--c-text)', fontSize: 12, boxSizing: 'border-box' }} />
          <textarea value={note} onChange={e => setNote(e.target.value)}
            placeholder="Note (optionnel)" rows={2}
            style={{ width: '100%', padding: '6px 8px', borderRadius: 6, border: '0.5px solid var(--c-border-med)', background: 'var(--c-bg-2)', color: 'var(--c-text)', fontSize: 12, resize: 'vertical', fontFamily: 'inherit', boxSizing: 'border-box' }} />
          <button onClick={validate} disabled={busy} style={{ ...primaryBtnStyle, background: 'var(--teal)', color: 'white', padding: '7px' }}>
            {busy ? '...' : 'Valider la course ✓'}
          </button>
        </div>
      )}
    </div>
  )
}

// ============ Exos ============

function ExercisesTab() {
  const [q, setQ] = useState('')
  const [category, setCategory] = useState('')
  const [data, setData] = useState(null)
  const [openId, setOpenId] = useState(null)

  useEffect(() => {
    const t = setTimeout(() => {
      api.exercises({ ...(q && { q }), ...(category && { category }) }).then(setData).catch(() => {})
    }, 300)
    return () => clearTimeout(t)
  }, [q, category])

  return (
    <>
      <input placeholder="Rechercher un exo..." value={q} onChange={e => setQ(e.target.value)} style={inputStyle} />
      <div style={{ display: 'flex', gap: 4, margin: '10px 0', overflowX: 'auto' }}>
        <FilterChip active={!category} onClick={() => setCategory('')}>Tout</FilterChip>
        {(data?.categories || []).map(c => (
          <FilterChip key={c} active={category === c} onClick={() => setCategory(c)}>{catFr(c)}</FilterChip>
        ))}
      </div>
      {(data?.exercises || []).map(ex => (
        <div key={ex.id} style={{ background: 'var(--c-bg)', border: '0.5px solid var(--c-border)', borderRadius: 10, padding: '10px 12px', marginBottom: 6 }}>
          <div onClick={() => setOpenId(openId === ex.id ? null : ex.id)} style={{ display: 'flex', gap: 10, cursor: 'pointer', alignItems: 'center' }}>
            {ex.image && <img src={ex.image} alt="" onError={e => { e.target.style.display = 'none' }} style={{ width: 44, height: 44, borderRadius: 6, objectFit: 'cover' }} loading="lazy" />}
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 500 }}>{cap(ex.name)}</div>
              <div style={{ fontSize: 11, color: 'var(--c-text-3)' }}>{catFr(ex.category)} · {equipFr(ex.equipment)}</div>
            </div>
          </div>
          {openId === ex.id && (
            <div style={{ marginTop: 8 }}>
              {ex.gif && <img src={ex.gif} alt="" onError={e => { e.target.style.display = 'none' }} style={{ width: '100%', borderRadius: 8, marginBottom: 6 }} loading="lazy" />}
              <InstructionsFR exerciseId={ex.id} fallback={ex.instructions} />
            </div>
          )}
        </div>
      ))}
    </>
  )
}

function InstructionsFR({ exerciseId, fallback }) {
  const [text, setText] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let alive = true
    api.exerciseFr(exerciseId)
      .then(d => { if (alive) setText(d.instructions_fr) })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [exerciseId])

  if (loading) return <div style={{ fontSize: 12, color: 'var(--c-text-3)' }}>Traduction...</div>
  return (
    <div style={{ fontSize: 12, color: 'var(--c-text-2)', lineHeight: 1.5 }}>
      {text || fallback}
      {!text && fallback && <div style={{ fontSize: 10, color: 'var(--c-text-3)', marginTop: 4 }}>(VF indisponible — serveur IA down)</div>}
    </div>
  )
}

function FilterChip({ active, onClick, children }) {
  return (
    <button onClick={onClick} style={{
      padding: '5px 12px', borderRadius: 20, fontSize: 12, cursor: 'pointer', whiteSpace: 'nowrap',
      border: '0.5px solid var(--c-border)', flexShrink: 0,
      background: active ? 'var(--c-text)' : 'transparent',
      color: active ? 'var(--c-bg)' : 'var(--c-text-2)',
    }}>{children}</button>
  )
}

// ============ Métriques ============

function MetricsTab({ showToast }) {
  const [logs, setLogs] = useState(null)
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10))

  const load = useCallback(() => api.logs().then(setLogs).catch(() => {}), [])
  useEffect(() => { load() }, [load])

  const addLog = async (type, value) => {
    if (!value || isNaN(value)) return false
    try {
      await api.log(type, parseFloat(value), date)
      showToast('Enregistré')
      load()
      return true
    } catch { showToast('Erreur sync', 'err'); return false }
  }

  if (!logs) return <Center small>Chargement...</Center>
  const last = t => logs[t]?.[0]?.value

  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 12 }}>
        <MetricCard label="Poids" value={last('poids') ?? '—'} unit="kg" />
        <MetricCard label="FC repos" value={last('fc') ?? '—'} unit="bpm" />
      </div>
      <Card title="Log">
        <div style={{ marginBottom: 8 }}>
          <input type="date" value={date} onChange={e => setDate(e.target.value)} style={{ ...inputStyle, width: '100%' }} />
        </div>
        <LogInput placeholder="Poids (kg)" onLog={v => addLog('poids', v)} unit="kg" step="0.1" />
        <div style={{ height: 8 }} />
        <LogInput placeholder="FC repos (bpm)" onLog={v => addLog('fc', v)} unit="bpm" />
      </Card>
      {logs.poids.length > 1 && (
        <>
          <div style={{ height: 12 }} />
          <Card title="Évolution du poids">
            <Sparkline points={[...logs.poids].reverse().slice(-20)} unit="kg" />
          </Card>
        </>
      )}
      {logs.fc.length > 1 && (
        <>
          <div style={{ height: 12 }} />
          <Card title="Évolution FC repos">
            <Sparkline points={[...logs.fc].reverse().slice(-20)} unit="bpm" color="var(--red)" />
          </Card>
        </>
      )}
      {logs.poids.length > 0 && (
        <>
          <div style={{ height: 12 }} />
          <Card title="Historique poids">
            {logs.poids.slice(0, 7).map((l, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', fontSize: 13, borderBottom: '0.5px solid var(--c-border)' }}>
                <span style={{ color: 'var(--c-text-2)' }}>{new Date(l.date).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })}</span>
                <span style={{ fontWeight: 500 }}>{l.value} kg</span>
              </div>
            ))}
          </Card>
        </>
      )}
    </>
  )
}

// ============ Profil ============

const EXCLUDABLE = [
  ['dual_cable', 'Double poulie (vis-à-vis)'],
  ['cable', 'Câble (simple poulie)'],
  ['dumbbell', 'Haltères'],
  ['smith machine', 'Smith machine'],
  ['barbell', 'Barre'],
  ['kettlebell', 'Kettlebell'],
]

function ProfileTab({ me, refreshMe, showToast }) {
  const [editing, setEditing] = useState(false)
  const [excluded, setExcluded] = useState(() => {
    try { return JSON.parse(me.profile.excluded_equipment || '["dual_cable"]') }
    catch { return ['dual_cable'] }
  })
  const [savingExcl, setSavingExcl] = useState(false)
  const p = me.profile

  const toggleExclusion = async (key) => {
    const next = excluded.includes(key) ? excluded.filter(k => k !== key) : [...excluded, key]
    setExcluded(next)
    setSavingExcl(true)
    try {
      await api.saveExclusions(next)
      showToast('Programme mis à jour')
    } catch (e) {
      showToast(e.message, 'err')
      setExcluded(excluded)
    } finally { setSavingExcl(false) }
  }

  const connectStrava = async () => {
    try {
      const { url } = await api.stravaAuth()
      window.location.href = url
    } catch (e) { showToast(e.message, 'err') }
  }

  if (editing) return <Onboarding initial={p} onDone={async () => { setEditing(false); await refreshMe() }} showToast={showToast} onCancel={() => setEditing(false)} />

  const labels = {
    goal: { recomp: 'Recomposition', strength: 'Force', endurance: 'Endurance' },
    focus: { balanced: 'Équilibré', upper: 'Haut du corps', lower: 'Bas du corps' },
    level: { beginner: 'Débutant', intermediate: 'Intermédiaire' },
    equipment_pref: { machines: 'Machines', machines_dumbbells: 'Machines + haltères', all: 'Tout' },
  }

  return (
    <>
      <Card title="Programme">
        <Row label="Objectif" value={labels.goal[p.goal]} />
        <Row label="Séances salle" value={`${p.gym_days}/semaine`} />
        <Row label="Focus" value={labels.focus[p.focus]} />
        <Row label="Niveau" value={labels.level[p.level]} />
        <Row label="Équipement" value={labels.equipment_pref[p.equipment_pref]} />
        <Row label="Course" value={p.run_days ? `${p.run_days}j — ${p.run_km_target} km/sem` : 'Non'} />
        <div style={{ height: 10 }} />
        <button onClick={() => setEditing(true)} style={ghostBtnStyle}>Modifier mes objectifs</button>
      </Card>
      <div style={{ height: 12 }} />
      <Card title="Matériel exclu">
        <div style={{ fontSize: 12, color: 'var(--c-text-2)', marginBottom: 10 }}>
          Coche ce que tu veux éviter (trop dur, toujours occupé...). La séance se régénère.
        </div>
        {EXCLUDABLE.map(([key, label]) => {
          const on = excluded.includes(key)
          return (
            <div key={key} onClick={() => !savingExcl && toggleExclusion(key)}
              style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '0.5px solid var(--c-border)', cursor: 'pointer', opacity: savingExcl ? 0.6 : 1 }}>
              <span style={{ fontSize: 13 }}>{label}</span>
              <div style={{
                width: 40, height: 22, borderRadius: 20, position: 'relative', transition: 'background 0.15s',
                background: on ? 'var(--red)' : 'var(--c-border-med)',
              }}>
                <div style={{
                  width: 18, height: 18, borderRadius: '50%', background: 'white',
                  position: 'absolute', top: 2, left: on ? 20 : 2, transition: 'left 0.15s',
                }} />
              </div>
            </div>
          )
        })}
        <div style={{ fontSize: 11, color: 'var(--c-text-3)', marginTop: 8 }}>
          Rouge = exclu du programme.
        </div>
      </Card>
      <div style={{ height: 12 }} />
      <Card title="Strava">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <div style={{ fontSize: 12, color: 'var(--c-text-2)' }}>Import automatique des activités</div>
          <StatusDot connected={me.strava_connected} />
        </div>
        <button onClick={connectStrava} style={ghostBtnStyle}>
          {me.strava_connected ? 'Reconnecter Strava ↗' : 'Connecter Strava ↗'}
        </button>
      </Card>
      <div style={{ height: 12 }} />
      <button onClick={() => { clearToken(); window.location.reload() }} style={{ ...ghostBtnStyle, width: '100%', color: 'var(--red)' }}>
        Déconnexion
      </button>
    </>
  )
}

// ============ UI helpers ============

function Center({ children, small }) {
  return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: small ? 120 : '100vh', color: 'var(--c-text-2)', fontSize: 14 }}>{children}</div>
}

function Card({ title, children }) {
  return (
    <div style={{ background: 'var(--c-bg)', border: '0.5px solid var(--c-border)', borderRadius: 12, padding: '1rem' }}>
      {title && <div style={{ fontSize: 11, fontWeight: 500, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--c-text-3)', marginBottom: 10 }}>{title}</div>}
      {children}
    </div>
  )
}

function MetricCard({ label, value, unit }) {
  return (
    <div style={{ background: 'var(--c-bg-2)', borderRadius: 8, padding: '0.75rem' }}>
      <div style={{ fontSize: 11, color: 'var(--c-text-2)', marginBottom: 4 }}>{label}</div>
      <div><span style={{ fontSize: 20, fontWeight: 500 }}>{value}</span><span style={{ fontSize: 11, color: 'var(--c-text-2)', marginLeft: 2 }}>{unit}</span></div>
    </div>
  )
}

function Sparkline({ points, unit, color = 'var(--teal)' }) {
  if (!points || points.length < 2) return null
  const w = 300, h = 80, pad = 6
  const values = points.map(p => p.value)
  const min = Math.min(...values), max = Math.max(...values)
  const range = max - min || 1
  const step = (w - pad * 2) / (points.length - 1)
  const coords = values.map((v, i) => [pad + i * step, h - pad - ((v - min) / range) * (h - pad * 2)])
  const path = coords.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const area = `${path} L${coords[coords.length - 1][0].toFixed(1)},${h - pad} L${coords[0][0].toFixed(1)},${h - pad} Z`
  const first = points[0], lastPt = points[points.length - 1]
  const delta = (lastPt.value - first.value)
  return (
    <div>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
        <path d={area} fill={color} opacity="0.12" />
        <path d={path} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {coords.map(([x, y], i) => (
          <circle key={i} cx={x} cy={y} r={i === coords.length - 1 ? 3 : 1.6} fill={color} />
        ))}
      </svg>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--c-text-3)', marginTop: 4 }}>
        <span>{new Date(first.date).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })}</span>
        <span style={{ fontWeight: 500, color: delta === 0 ? 'var(--c-text-2)' : delta < 0 ? 'var(--green)' : 'var(--red)' }}>
          {delta > 0 ? '+' : ''}{delta.toFixed(1)} {unit}
        </span>
        <span>{new Date(lastPt.date).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })}</span>
      </div>
    </div>
  )
}

function BarChart({ bars, unit, color = 'var(--teal)' }) {
  const max = Math.max(...bars.map(b => b.value), 1)
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 6, height: 90 }}>
      {bars.map((b, i) => (
        <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
          <div style={{ fontSize: 10, color: 'var(--c-text-3)' }}>{b.value > 0 ? (unit === 'km' ? b.value.toFixed(1) : b.value) : ''}</div>
          <div style={{
            width: '100%', maxWidth: 22, borderRadius: 4,
            height: Math.max(2, (b.value / max) * 56),
            background: b.value > 0 ? color : 'var(--c-bg-2)',
          }} />
          <div style={{ fontSize: 9, color: 'var(--c-text-3)' }}>{b.label}</div>
        </div>
      ))}
    </div>
  )
}

function Row({ label, value }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', borderBottom: '0.5px solid var(--c-border)', fontSize: 13 }}>
      <span style={{ color: 'var(--c-text-2)' }}>{label}</span>
      <span style={{ fontWeight: 500 }}>{value}</span>
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
        style={{ ...inputStyle, flex: '1 1 auto', minWidth: 0, width: 'auto' }} />
      <button onClick={submit} style={{ ...ghostBtnStyle, padding: '8px 16px', flexShrink: 0, whiteSpace: 'nowrap' }}>+ {unit}</button>
    </div>
  )
}

const inputStyle = {
  width: '100%', padding: '10px 12px', borderRadius: 8,
  border: '0.5px solid var(--c-border-med)', background: 'var(--c-bg-2)',
  color: 'var(--c-text)', fontSize: 14,
}
const primaryBtnStyle = {
  width: '100%', padding: '11px', borderRadius: 8, border: 'none',
  background: 'var(--c-text)', color: 'var(--c-bg)', fontSize: 14,
  fontWeight: 500, cursor: 'pointer',
}
const ghostBtnStyle = {
  padding: '9px 14px', borderRadius: 8, border: '0.5px solid var(--c-border-med)',
  background: 'transparent', color: 'var(--c-text)', fontSize: 13, cursor: 'pointer',
  width: '100%', textAlign: 'center',
}
