const BASE = import.meta.env.VITE_API_URL || '/api'
const TOKEN = import.meta.env.VITE_APP_TOKEN || ''

async function req(method, path, body) {
  const opts = {
    method,
    headers: { 'Authorization': `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
  }
  if (body !== undefined) opts.body = JSON.stringify(body)
  const r = await fetch(`${BASE}${path}`, opts)
  if (!r.ok) throw new Error(`${method} ${path} → ${r.status}`)
  return r.json()
}

export const api = {
  getState: () => req('GET', '/state'),
  setState: (data) => req('POST', '/state', data),
  log: (type, value) => req('POST', `/log/${type}`, { value, date: new Date().toISOString() }),
  toggleSession: (week_num, year, day_index, done) =>
    req('POST', '/sessions/toggle', { week_num, year, day_index, done }),
  saveCharges: (charges) => req('POST', '/charges', charges),

  stravaStatus: () => req('GET', '/strava/status'),
  stravaAuth: () => req('GET', '/strava/auth'),
  stravaActivities: () => req('GET', '/strava/activities'),

  googleStatus: () => req('GET', '/google/status'),
  googleAuth: () => req('GET', '/google/auth'),
  createCalendarEvents: (opts) => req('POST', '/google/create-events', opts),
}
