// The Desktop half rendered with React's server renderer, against plugin tool output shapes.
import assert from 'node:assert/strict'
import { cpSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { setFixture } from '@hermes/plugin-sdk'
import { createElement as h } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

// plugin.js imports bare specifiers; a copy beside node_modules resolves them here.
const here = dirname(fileURLToPath(import.meta.url))
mkdirSync(join(here, '.build'), { recursive: true })
cpSync(join(here, '../../desktop/plugin.js'), join(here, '.build/plugin.mjs'))
const { default: plugin, TasksView } = await import('./.build/plugin.mjs')

const items = []
plugin.register({ rest: async () => ({}), onEvent() {}, registerMany: xs => items.push(...xs), register: x => items.push(x) })
const render = id => {
  const item = items.find(i => i.id === id)
  return renderToStaticMarkup(h(() => (item.render ? item.render() : item.data.render(item.props))))
}
const text = html => html.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, "'").replace(/\s+/g, ' ').trim()

const day = n => {
  const d = new Date()
  d.setHours(12, 0, 0, 0)
  d.setDate(d.getDate() + n)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
// A local wall-clock stamp `minutes` before now, so "N min ago" holds at any hour of the run.
const ago = minutes => {
  const d = new Date(Date.now() - minutes * 60000)
  const p = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:00`
}
const monday = (() => {
  const d = new Date()
  d.setHours(12, 0, 0, 0)
  return day(-((d.getDay() + 6) % 7))
})()
const weekdays = Object.fromEntries(['mon', 'tue', 'wed', 'thu', 'fri'].map(k => [k, [{ start: '09:00', end: '18:00' }]]))
const status = (calendars, push = null) => ({
  ok: true,
  ready: true,
  problems: [],
  engine: { path: '/x/solverforge-planner' },
  timezone: 'Europe/Rome',
  availability: weekdays,
  calendars,
  clashes: [],
  publish_to: push?.calendar ?? null,
  push,
  local_calendar: '/home/u/.hermes/plugin-data/solverforge-planner/exports/planner.ics'
})
const proposal = (blocks, extra = {}) => ({
  ok: true,
  proposal_id: '808f1223-73c5-4a48-bade-076a05225e07',
  score: '0hard/0medium/0soft',
  hard_rules_kept: true,
  can_apply: true,
  blocked_because: [],
  applied_at: null,
  calendars: [],
  scheduled: blocks.map(([d, s, e, title]) => ({ task_id: title, title, start: `${d} ${s}:00`, end: `${d} ${e}:00`, day: d })),
  unscheduled: [],
  ...extra
})

test('the page opens on the week: calendar, applied blocks and a pending proposal', () => {
  setFixture({
    status: status([{ source: 'google: primary', read_at: ago(90), window: [day(0), day(3)] }],
                   { calendar: 'google: primary', create: 1, delete: 0 }),
    proposal: proposal([[day(0), '15:00', '16:00', 'Write report']]),
    weeks: {
      [monday]: {
        ok: true,
        events: [
          { kind: 'busy', title: 'Standup', start: `${day(0)} 09:00:00`, end: `${day(0)} 09:30:00`, source: 'google: primary' },
          { kind: 'planned', title: 'Call accountant', start: `${day(0)} 11:00:00`, end: `${day(0)} 11:30:00`, task_id: 'a' }
        ]
      }
    }
  })
  const html = render('page')
  const shown = text(html)
  assert.match(html, /data-on="true">Week</)
  assert.match(shown, /This week/)
  assert.match(shown, /google: primary · \d+ (min|h) ago/)
  assert.match(shown, /1 change\(s\) to push to google: primary/)
  assert.match(shown, /The planner's calendar: \/home\/u\/.+\/planner\.ics/)
  assert.doesNotMatch(shown, /Refresh|Pull|Push now/, 'no button for what only Hermes can do')
  assert.match(html, /Standup {2}09:00–09:30 \(busy\)/)
  assert.match(html, /Call accountant {2}11:00–11:30 \(on your plan\)/)
  assert.match(html, /Write report {2}15:00–16:00 \(proposed\)/)
  assert.match(shown, /Proposal 0hard\/0medium\/0soft 1 placed/)
})

test('days after the last read are marked unread; past days are not', () => {
  setFixture({
    status: status([{ source: 'google: work', read_at: `${day(0)} 08:00:00`, window: [day(0), day(1)] }]),
    proposal: { ok: false },
    weeks: {}
  })
  const html = render('page')
  const later = [1, 2, 3, 4, 5, 6].map(day).filter(d => d < day(7 - ((new Date().getDay() + 6) % 7)))
  const columns = (html.match(/repeating-linear-gradient/g) || []).length
  const unreadWorkdays = later.filter(d => [1, 2, 3, 4, 5].includes(new Date(`${d}T12:00:00`).getDay()))
  assert.equal(columns, unreadWorkdays.length)
  assert.equal((html.match(/· not read/g) || []).length, unreadWorkdays.length)
})

test('with no calendar pulled the week says so instead of looking free', () => {
  setFixture({ status: status([]), proposal: { ok: false }, weeks: {} })
  const shown = text(render('page'))
  assert.match(shown, /No calendar pulled\. Ask Hermes to pull your calendar, or tell it when you are busy\./)
  assert.doesNotMatch(shown, /to push to|Refresh|Pull/)
})

test('overlaps sit side by side: double-bookings in amber, clashes with planned blocks in red', () => {
  const d = day(1)
  const read = { source: 'google: primary', read_at: `${day(0)} 00:00:01`, window: [day(0), day(28)] }
  setFixture({
    status: status([read, { ...read, source: 'google: Family' }]),
    proposal: { ok: false },
    weeks: {
      [monday]: {
        ok: true,
        events: [
          { kind: 'busy', title: 'Standup', start: `${d} 09:00:00`, end: `${d} 09:30:00`, source: 'google: primary' },
          { kind: 'busy', title: 'Dentist', start: `${d} 09:15:00`, end: `${d} 10:00:00`, source: 'google: Family' },
          { kind: 'busy', title: 'Board', start: `${d} 14:00:00`, end: `${d} 15:00:00`, source: 'google: primary' },
          { kind: 'busy', title: 'Board', start: `${d} 14:00:00`, end: `${d} 15:00:00`, source: 'google: Family' },
          { kind: 'busy', title: 'Client call', start: `${d} 16:00:00`, end: `${d} 16:30:00`, source: 'google: primary' },
          { kind: 'planned', title: 'Write report', start: `${d} 16:00:00`, end: `${d} 17:00:00`, task_id: 't' }
        ],
        clashes: [{ task_id: 't', title: 'Write report', start: `${d} 16:00:00`, end: `${d} 17:00:00`, with: 'Client call',
                    with_start: `${d} 16:00:00`, with_end: `${d} 16:30:00`, calendar: 'google: primary' }]
      }
    }
  })
  const html = render('page')
  const shown = text(html)
  if (d >= day(7 - ((new Date().getDay() + 6) % 7))) return // tomorrow is next week: nothing to draw here
  assert.equal((html.match(/data-mark="double-booked"/g) || []).length, 2, 'standup and dentist overlap')
  assert.equal((html.match(/data-mark="clash"/g) || []).length, 2, 'the call and the planned block')
  assert.equal((html.match(/Board {2}14:00–15:00/g) || []).length, 1, 'the same invite on two calendars is one block')
  assert.match(html, /on google: primary and google: Family/)
  assert.match(html, /width:calc\(50% - 2px\)/, 'overlapping events share the column')
  assert.match(shown, /Write report clashes with Client call/)
  assert.match(shown, /Write report clashes with Client call · .+ Re-plan/)
  assert.doesNotMatch(shown, /Re-plan with Hermes/)
})

test('weekend columns appear only when there is something on them', () => {
  // This week's Saturday: five days after its Monday, whichever day the run falls on.
  const saturday = day(5 - ((new Date().getDay() + 6) % 7))
  setFixture({ status: status([]), proposal: { ok: false }, weeks: {} })
  assert.doesNotMatch(text(render('page')), /Sat,/)
  {
    setFixture({
      status: status([]),
      proposal: { ok: false },
      weeks: { [monday]: { ok: true, events: [{ kind: 'busy', title: 'Hike', start: `${saturday} 10:00:00`, end: `${saturday} 12:00:00` }] } }
    })
    assert.match(text(render('page')), /Sat,/)
  }
})

test('the chat plan card renders the proposal it names', () => {
  setFixture({ status: status([]), proposal: proposal([[day(1), '09:00', '10:00', 'Deck']]), weeks: {} })
  const item = items.find(i => i.id === 'plan-card')
  item.props = { attrs: { proposal: '808f1223-73c5-4a48-bade-076a05225e07' } }
  assert.match(text(render('plan-card')), /Plan proposal 0hard\/0medium\/0soft 1 placed/)
})

test('the tasks tab sorts tasks into to plan, on your calendar and past, and says how old the calendar is', () => {
  const task = (id, title, extra = {}) => ({
    id, title, state: 'inbox', duration_minutes: 60, priority: 'normal', cognitive_load: 'medium',
    earliest_at: null, deadline_kind: 'none', deadline_at: null, ...extra
  })
  const inbox = {
    ok: true,
    tasks: [
      task('pr', 'Review PR', { duration_minutes: 90 }),
      task('post', 'Launch post', { deadline_kind: 'hard', deadline_at: `${day(4)} 18:00:00`, cognitive_load: 'high' }),
      task('deck', 'Q4 deck', { state: 'applied', duration_minutes: 240,
                                block: { start: `${day(1)} 09:00:00`, end: `${day(1)} 13:00:00` } }),
      task('old', 'Old call', { state: 'applied', duration_minutes: 30,
                                block: { start: `${day(-2)} 10:00:00`, end: `${day(-2)} 10:30:00` } })
    ],
    order_rules: [{ before: 'Q4 deck', after: 'Review PR', before_id: 'deck', after_id: 'pr' }]
  }
  const st = status([{ source: 'google: primary', read_at: `${day(0)} 00:00:00`, window: [day(0), day(7)] }])
  const html = renderToStaticMarkup(h(TasksView, { status: st, inbox }))
  const section = name => text(html.split(`data-section="${name}"`)[1].replace(/^[^>]*>/, '').split('<section')[0])

  assert.match(section('To plan'), /^To plan 2 /)
  assert.match(section('To plan'), /1 h 30 min Review PR after Q4 deck/)
  assert.match(section('To plan'), /1 h Launch post deep work · due \S+ \S+ \d+ 18:00/)
  assert.match(section('On your calendar'), /^On your calendar 1 google: primary · pulled .+ Tomorrow /)
  assert.match(section('On your calendar'), /Tomorrow 09:00–13:00 Q4 deck 4 h$/)
  assert.doesNotMatch(section('On your calendar'), /Old call/)
  // Past blocks are folded away behind their count.
  assert.match(section('Past'), /▸ Past · 1/)
  assert.doesNotMatch(section('Past'), /Old call/)
})

test('with no tasks the tab says how to start', () => {
  const html = renderToStaticMarkup(h(TasksView, { status: status([]), inbox: { ok: true, tasks: [], order_rules: [] } }))
  assert.match(text(html), /No tasks yet ?Tell Hermes what you need to get done this week\./)
})
