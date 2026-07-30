const BASE = import.meta.env.VITE_API_URL || '/api'

export function getToken() { return localStorage.getItem('fitlife_token') }
export function setToken(t) { localStorage.setItem('fitlife_token', t) }
export function clearToken() { localStorage.removeItem('fitlife_token') }

async function req(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } }
  const token = getToken()
  if (token) opts.headers['Authorization'] = `Bearer ${token}`
  if (body !== undefined) opts.body = JSON.stringify(body)
  const r = await fetch(`${BASE}${path}`, opts)
  if (r.status === 401 && !path.startsWith('/auth/')) {
    clearToken()
    window.location.reload()
  }
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.error || `${method} ${path} → ${r.status}`)
  return data
}

export const api = {
  register: (email, password) => req('POST', '/auth/register', { email, password }),
  login: (email, password) => req('POST', '/auth/login', { email, password }),
  me: () => req('GET', '/me'),
  saveProfile: (p) => req('POST', '/profile', p),
  saveExclusions: (excluded) => req('POST', '/profile/exclusions', { excluded }),

  exerciseFr: (id) => req('GET', `/exercises/${id}/fr`),
  exercises: (params = {}) => {
    const qs = new URLSearchParams(params).toString()
    return req('GET', `/exercises${qs ? '?' + qs : ''}`)
  },

  workouts: () => req('GET', '/workouts'),
  generateWorkout: () => req('POST', '/workouts/generate'),
  setMode: (mode) => req('POST', '/workouts/mode', { mode }),
  updateSet: (workoutId, setId, data) => req('POST', `/workouts/${workoutId}/sets/${setId}`, data),
  completeWorkout: (workoutId, data = {}) => req('POST', `/workouts/${workoutId}/complete`, data),

  log: (type, value, date) => req('POST', `/log/${type}`, { value, date }),
  logs: () => req('GET', '/logs'),
  progress: () => req('GET', '/progress'),
}
