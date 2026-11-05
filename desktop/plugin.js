/**
 * SolverForge Planner — Desktop half.
 *
 * Surfaces: the ::planner{proposal="…"} plan card in the transcript, a Planner
 * page (the week: calendar, applied blocks, a pending proposal) with its sidebar row, a status-bar chip, palette commands and live
 * refresh on plugin events. Every byte of data comes from this package's own
 * backend (dashboard/plugin_api.py) through ctx.rest; the only writes are Apply
 * and Install engine, each behind a confirmation the user answers.
 */

import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  GlyphSpinner,
  host,
  PALETTE_AREA,
  PanelEmpty,
  queryClient,
  ROUTES_AREA,
  SegmentedControl,
  SIDEBAR_NAV_AREA,
  STATUSBAR_AREAS,
  Tip,
  TRANSCRIPT_DIRECTIVE_AREA,
  useMutation,
  useQuery,
  useQueryClient
} from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'solverforge-planner'
const ROUTE = '/planner'
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const DAY_START = 7 // grid hours shown, extended to fit any block outside them
const DAY_END = 20

let rest = () => Promise.reject(new Error('SolverForge Planner not loaded'))

const text = (color, size = '0.75rem') => ({ color: `var(${color})`, fontSize: size })
const qk = (...parts) => [ID, ...parts]

function useProposal(id) {
  return useQuery({
    queryKey: qk('proposal', id || 'latest'),
    queryFn: () => rest(id ? `/proposal?id=${encodeURIComponent(id)}` : '/proposal'),
    staleTime: 5_000
  })
}

function useWeek(start, days) {
  return useQuery({
    enabled: Boolean(start),
    queryKey: qk('week', start, days),
    queryFn: () => rest(`/week?start=${encodeURIComponent(start)}&days=${days}`),
    staleTime: 5_000
  })
}

function useApply() {
  const client = useQueryClient()

  return useMutation({
    mutationFn: id => rest('/apply', { method: 'POST', body: { id } }),
    onSuccess: result => {
      client.invalidateQueries({ queryKey: [ID] })
      if (result?.ok) {
        host.notify({
          kind: 'success',
          message: `Planner: ${result.written.length} block(s) on your plan${
            result.push_to ? `. Ask Hermes to update ${result.push_to} to put them there too.` : ''
          }`
        })
      } else {
        host.notify({ kind: 'error', message: `Planner: ${plainError(result, 'apply refused')}` })
      }
    }
  })
}

const minutes = stamp => Number(stamp.slice(11, 13)) * 60 + Number(stamp.slice(14, 16))
const dayKey = stamp => stamp.slice(0, 10)
const dayLabel = key =>
  new Date(`${key}T12:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short', weekday: 'short' })
const hhmm = stamp => stamp.slice(11, 16)

/** Days covered by the blocks, in order. */
function daysOf(blocks) {
  return [...new Set(blocks.map(b => dayKey(b.start)))].sort()
}

const isoDay = d =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

const KINDS = {
  busy: { suffix: ' (busy)', fill: '--ui-text-quaternary', mix: 18, ink: '--ui-text-tertiary', z: 1 },
  applied: { suffix: ' (on your plan)', fill: '--ui-accent', mix: 12, ink: '--ui-text-secondary', z: 2 },
  proposed: { suffix: ' (proposed)', fill: '--ui-accent', mix: 26, ink: '--ui-text-primary', z: 3, dashed: true }
}
const AMBER = 'var(--ui-warning, #d89614)'
const RED = 'var(--ui-destructive, #e5484d)'
// Calendar edges: the user's own calendar keeps the neutral edge, the others get distinct hues.
const HUES = [205, 145, 280, 25, 330, 95]
const calendarEdge = (source, sources) => {
  const i = sources.indexOf(source)
  return i <= 0 ? 'var(--ui-stroke-secondary)' : `hsl(${HUES[(i - 1) % HUES.length]} 55% 55%)`
}
const endMinute = b => (dayKey(b.end) > dayKey(b.start) ? 1440 : minutes(b.end))
const overlaps = (a, b) => a.start < b.end && b.start < a.end

/**
 * Side-by-side lanes for one day, the way calendar apps draw overlapping events: each cluster of
 * overlapping items is split into as many lanes as it needs at its busiest.
 */
function lanes(items) {
  const sorted = [...items].sort((a, b) => a.start.localeCompare(b.start) || b.end.localeCompare(a.end))
  const placed = []
  let cluster = []
  let clusterEnd = ''
  const close = () => {
    const width = Math.max(1, ...cluster.map(c => c.lane + 1))
    cluster.forEach(c => (c.lanes = width))
    cluster = []
  }
  for (const item of sorted) {
    if (cluster.length && item.start >= clusterEnd) {
      close()
    }
    const busyLanes = new Set(cluster.filter(c => overlaps(c.item, item)).map(c => c.lane))
    let lane = 0
    while (busyLanes.has(lane)) {
      lane += 1
    }
    const entry = { item, lane, lanes: 1 }
    cluster.push(entry)
    placed.push(entry)
    clusterEnd = cluster.length === 1 || item.end > clusterEnd ? item.end : clusterEnd
  }
  if (cluster.length) {
    close()
  }
  return placed
}

/**
 * The same meeting on two calendars is one busy block: identical title and time collapse into
 * one, which lists every calendar it came from.
 */
function mergeDuplicates(busy) {
  const byKey = new Map()
  for (const b of busy) {
    const key = `${b.start}|${b.end}|${b.title.trim().toLowerCase()}`
    const seen = byKey.get(key)
    if (seen) {
      seen.sources.push(b.source)
    } else {
      byKey.set(key, { ...b, sources: [b.source] })
    }
  }
  return [...byKey.values()]
}

/**
 * A compact day-column grid. Busy time is muted with a coloured edge per calendar, blocks already
 * on the plan are in a light accent, proposed blocks in the full accent with a dashed edge.
 * Overlapping items sit side by side. Two of the user's own events at once are double-booked
 * (amber); an event on top of a planned block is a clash (red). Days in ``unread`` are outside
 * every calendar read and are hatched, so an empty day never passes for a free one.
 */
function WeekGrid({ days, busy = [], applied = [], proposed = [], unread = new Set(), sources = [], height = 240 }) {
  if (!days.length) {
    return null
  }
  const events = mergeDuplicates(busy)
  const all = [...events, ...applied, ...proposed].filter(b => days.includes(dayKey(b.start)))
  const lo = Math.min(DAY_START * 60, ...all.map(b => minutes(b.start)))
  const hi = Math.max(DAY_END * 60, ...all.map(endMinute))
  const span = Math.max(60, hi - lo)
  const y = m => `${((Math.max(lo, m) - lo) / span) * 100}%`
  // Blocks keep their true length so back-to-back blocks never overlap; a block
  // too short for a line of text drops its label and keeps it in the tooltip.
  const labelFits = (start, end) => ((end - start) / span) * height >= 13
  const today = isoDay(new Date())

  const place = ({ item: b, lane, lanes: width }) => {
    const k = KINDS[b.kind]
    const top = minutes(b.start)
    const bottom = endMinute(b)
    const notes = []
    let ring
    if (b.kind === 'busy') {
      if (applied.some(a => overlaps(a, b))) {
        ring = RED
        notes.push('clashes with a planned block')
      } else if (events.some(o => o !== b.ref && overlaps(o, b))) {
        ring = AMBER
        notes.push('double-booked')
      }
      if (b.sources.length > 1) {
        notes.push(`on ${b.sources.map(s => s).join(' and ')}`)
      }
    } else if (b.kind === 'applied' && events.some(o => overlaps(o, b))) {
      ring = RED
      notes.push('an event is now on top of this block')
    }
    const edge = b.kind === 'busy' ? calendarEdge(b.sources[0], sources) : 'var(--ui-accent)'
    return jsx(
      Tip,
      {
        label: `${b.title}  ${hhmm(b.start)}–${hhmm(b.end)}${k.suffix}${notes.length ? ` · ${notes.join(' · ')}` : ''}`,
        children: jsx('div', {
          'data-kind': b.kind,
          'data-mark': ring === RED ? 'clash' : ring === AMBER ? 'double-booked' : undefined,
          style: {
            position: 'absolute',
            left: `calc(${(lane / width) * 100}% + 1px)`,
            width: `calc(${100 / width}% - 2px)`,
            top: `calc(${y(top)} + 1px)`,
            height: `max(3px, calc(${y(bottom)} - ${y(top)} - 2px))`,
            borderRadius: 3,
            overflow: 'hidden',
            padding: '1px 4px',
            background: `color-mix(in srgb, var(${k.fill}) ${k.mix}%, transparent)`,
            borderLeft: `2px solid ${edge}`,
            outline: ring ? `1.5px solid ${ring}` : k.dashed ? '1px dashed var(--ui-accent)' : undefined,
            outlineOffset: -1,
            ...text(k.ink, '0.65rem'),
            lineHeight: 1.25,
            zIndex: k.z
          },
          children: labelFits(top, bottom) ? `${hhmm(b.start)} ${b.title}` : null
        })
      },
      `${b.kind}-${b.start}-${b.title}-${lane}`
    )
  }

  const hours = []
  for (let h = Math.ceil(lo / 60); h * 60 <= hi; h += 2) {
    hours.push(h)
  }
  const tagged = [
    ...events.map(b => ({ ...b, kind: 'busy', ref: b })),
    ...applied.map(b => ({ ...b, kind: 'applied' })),
    ...proposed.map(b => ({ ...b, kind: 'proposed' }))
  ]

  return jsxs('div', {
    style: { display: 'grid', gridTemplateColumns: `2.25rem repeat(${days.length}, minmax(0, 1fr))`, gap: 4 },
    children: [
      jsx('div', {}),
      ...days.map(d =>
        jsxs(
          'div',
          {
            style: {
              ...text(d === today ? '--ui-accent' : d < today ? '--ui-text-quaternary' : '--ui-text-secondary', '0.7rem'),
              fontWeight: 500
            },
            children: [
              dayLabel(d),
              unread.has(d) &&
                jsx('span', { style: { ...text('--ui-text-quaternary', '0.6rem'), fontWeight: 400 }, children: ' · not read' })
            ]
          },
          `h-${d}`
        )
      ),
      jsx('div', {
        style: { position: 'relative', height },
        children: hours.map(h =>
          jsx(
            'div',
            {
              style: { position: 'absolute', top: y(h * 60), ...text('--ui-text-quaternary', '0.6rem'), transform: 'translateY(-50%)' },
              children: `${String(h).padStart(2, '0')}:00`
            },
            h
          )
        )
      }),
      ...days.map(d =>
        jsx(
          'div',
          {
            'data-day': d,
            style: {
              position: 'relative',
              height,
              borderRadius: 4,
              opacity: d < today ? 0.55 : 1,
              background: unread.has(d)
                ? 'repeating-linear-gradient(135deg, color-mix(in srgb, var(--ui-stroke-tertiary) 30%, transparent) 0 6px, transparent 6px 12px)'
                : 'color-mix(in srgb, var(--ui-stroke-tertiary) 25%, transparent)'
            },
            children: lanes(tagged.filter(b => dayKey(b.start) === d)).map(place)
          },
          `c-${d}`
        )
      )
    ]
  })
}

function ScoreBadge({ plan }) {
  if (!plan) {
    return null
  }
  return jsx(Tip, {
    label: plan.hard_rules_kept
      ? 'Every hard rule holds: working hours, busy time, deadlines, task order.'
      : 'Some hard rule is broken; see the tasks below.',
    children: jsx(Badge, { variant: plan.hard_rules_kept ? 'success' : 'destructive', children: plan.score })
  })
}

function ApplyButton({ plan }) {
  const [open, setOpen] = useState(false)
  const apply = useApply()

  if (plan.applied_at) {
    return jsx(Badge, { variant: 'muted', children: 'Applied' })
  }
  if (!plan.can_apply) {
    return jsx(Tip, {
      label: `Cannot apply: ${plan.blocked_because.join(', ') || 'stale'}. Solve again.`,
      children: jsx(Badge, { variant: 'warn', children: 'Out of date' })
    })
  }

  return jsxs('span', {
    children: [
      jsx(Button, {
        size: 'xs',
        disabled: apply.isPending || !plan.scheduled.length,
        onClick: () => setOpen(true),
        children: apply.isPending ? 'Applying…' : `Apply ${plan.scheduled.length}`
      }),
      jsx(ConfirmDialog, {
        open,
        onClose: () => setOpen(false),
        title: `Put ${plan.scheduled.length} block(s) on your plan?`,
        description: [
          ...plan.scheduled.map(b => `${b.day} ${hhmm(b.start)}–${hhmm(b.end)}  ${b.title}`),
          '',
          plan.publish_to
            ? `They go on the planner's calendar. To put them on ${plan.publish_to} too, ask Hermes to update it afterwards.`
            : "They go on the planner's calendar."
        ].join('\n'),
        confirmLabel: 'Apply',
        busyLabel: 'Applying…',
        onConfirm: () => apply.mutateAsync(plan.proposal_id)
      })
    ]
  })
}

function InstallButton() {
  const [open, setOpen] = useState(false)
  const client = useQueryClient()
  const install = useMutation({
    mutationFn: () => rest('/install', { method: 'POST', body: {}, timeoutMs: 180_000 }),
    onSuccess: result => {
      client.invalidateQueries({ queryKey: [ID] })
      host.notify(
        result?.ok
          ? { kind: 'success', message: 'Planner: engine installed' }
          : { kind: 'error', message: `Planner: ${result?.error || 'install failed'}` }
      )
    }
  })

  return jsxs('span', {
    children: [
      jsx(Button, {
        size: 'xs',
        disabled: install.isPending,
        onClick: () => setOpen(true),
        children: install.isPending ? 'Installing…' : 'Install engine'
      }),
      jsx(ConfirmDialog, {
        open,
        onClose: () => setOpen(false),
        title: 'Download the SolverForge engine?',
        description:
          'Downloads one binary for this computer from github.com/blackopsrepl/hermes-solverforge-planner releases and refuses it unless its SHA-256 matches the checksum pinned in this plugin.',
        confirmLabel: 'Install',
        busyLabel: 'Installing…',
        onConfirm: () => install.mutateAsync()
      })
    ]
  })
}

function Unscheduled({ plan }) {
  if (!plan.unscheduled.length) {
    return null
  }
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 2 },
    children: [
      jsx('div', { style: { ...text('--ui-text-secondary', '0.7rem'), fontWeight: 500 }, children: 'Did not fit' }),
      ...plan.unscheduled.map(u =>
        jsx(
          'div',
          {
            style: text('--ui-text-tertiary', '0.7rem'),
            children: `${u.title} — ${(u.outcome || 'no free slot').replaceAll('_', ' ')}${
              u.blockers?.length ? ` (blocked by ${u.blockers.map(b => b.title || b).join(', ')})` : ''
            }`
          },
          u.task_id
        )
      )
    ]
  })
}

function PlanView({ plan, compact }) {
  const days = daysOf(plan.scheduled)
  const busy = useWeek(days[0], days.length ? Math.max(1, (new Date(days.at(-1)) - new Date(days[0])) / 864e5 + 1) : 1)
  const busyEvents = (busy.data?.events || []).filter(e => e.kind === 'busy')
  const appliedEvents = plan.applied_at ? [] : (busy.data?.events || []).filter(e => e.kind === 'planned')

  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 8 },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
        children: [
          jsx('span', { style: { ...text('--ui-text-primary', '0.8rem'), fontWeight: 600 }, children: 'Plan proposal' }),
          jsx(ScoreBadge, { plan }),
          jsx('span', {
            style: text('--ui-text-tertiary', '0.7rem'),
            children: `${plan.scheduled.length} placed${plan.unscheduled.length ? `, ${plan.unscheduled.length} not placed` : ''}`
          }),
          jsx('span', { style: { flex: 1 } }),
          jsx(ApplyButton, { plan })
        ]
      }),
      plan.scheduled.length
        ? jsx(WeekGrid, {
            days,
            busy: busyEvents,
            applied: plan.applied_at ? plan.scheduled : appliedEvents,
            proposed: plan.applied_at ? [] : plan.scheduled,
            height: compact ? 300 : 460
          })
        : jsx('div', { style: text('--ui-text-tertiary'), children: 'Nothing scheduled.' }),
      jsx(Unscheduled, { plan })
    ]
  })
}

function PlanCard({ attrs, streaming }) {
  const id = attrs?.proposal || attrs?.id || ''
  const valid = UUID.test(id)
  const query = useProposal(valid && !streaming ? id : null)

  if (!valid) {
    return null // untrusted attrs: render nothing rather than guess
  }
  const frame = children =>
    jsx('div', {
      style: {
        border: '1px solid var(--ui-stroke-secondary)',
        borderRadius: 6,
        padding: 10,
        margin: '6px 0',
        maxWidth: 760
      },
      children
    })

  if (streaming || query.isLoading) {
    return frame(jsx(GlyphSpinner, {}))
  }
  if (!query.data?.ok) {
    return frame(jsx('div', { style: text('--ui-text-tertiary'), children: query.data?.error || 'Proposal unavailable.' }))
  }
  return frame(jsx(PlanView, { plan: query.data, compact: true }))
}


/** A refusal as the user should read it. The planner's own messages are written for the agent. */
const plainError = (result, fallback) => {
  if (result?.code === 'calendar_stale') {
    const names = (result.pull || []).join(', ') || 'your calendar'
    return `Your copy of ${names} is too old for this. Ask Hermes to pull it, then try again.`
  }
  if (result?.code === 'calendar_conflict') return 'Something new on your calendar overlaps this plan. Solve again.'
  if (result?.code === 'stale') return 'Your tasks or settings changed since this plan. Solve again.'
  return result?.error || fallback
}

const ageOf = stamp => {
  const minutes = Math.round((Date.now() - new Date(stamp.replace(' ', 'T')).getTime()) / 60_000)
  return minutes < 1 ? 'just now' : minutes < 60 ? `${minutes} min ago` : minutes < 1440 ? `${Math.round(minutes / 60)} h ago` : `${Math.round(minutes / 1440)} d ago`
}

/** One chip per pulled calendar: its colour and how old the copy is. Pulls are done by Hermes. */
function CalendarChips({ calendars, sources }) {
  return jsx('span', {
    style: { display: 'inline-flex', gap: 6, flexWrap: 'wrap' },
    children: calendars.map(c =>
      jsx(
        Tip,
        {
          label: `${c.source}: pulled ${c.read_at.slice(0, 16)}. Ask Hermes to pull it again for a newer copy.`,
          children: jsx(Badge, {
            variant: 'muted',
            children: jsxs('span', {
              style: { display: 'inline-flex', alignItems: 'center', gap: 4 },
              children: [
                jsx('span', { style: { width: 6, height: 6, borderRadius: 3, background: calendarEdge(c.source, sources) } }),
                c.source,
                ` · ${ageOf(c.read_at)}`
              ]
            })
          })
        },
        c.source
      )
    )
  })
}

/** Take the clashing tasks off the plan and solve again, after the user confirms. */
function ReplanButton({ clashes }) {
  const [open, setOpen] = useState(false)
  const client = useQueryClient()
  const tasks = [...new Map(clashes.map(c => [c.task_id, c])).values()]
  const replan = useMutation({
    mutationFn: async () => {
      for (const task of tasks) {
        const result = await rest('/unschedule', { method: 'POST', body: { id: task.task_id } })
        if (!result?.ok) return result
      }
      return rest('/optimize', { method: 'POST', body: {}, timeoutMs: 180_000 })
    },
    onSuccess: result => {
      client.invalidateQueries({ queryKey: [ID] })
      host.notify(
        result?.ok
          ? { kind: 'success', message: `Planner: new proposal ${result.score}; apply it to put the tasks back` }
          : { kind: 'error', message: `Planner: ${plainError(result, 're-plan failed')}` }
      )
    }
  })
  return jsxs('span', {
    children: [
      jsx(Button, { size: 'xs', disabled: replan.isPending, onClick: () => setOpen(true), children: replan.isPending ? 'Re-planning…' : 'Re-plan' }),
      jsx(ConfirmDialog, {
        open,
        onClose: () => setOpen(false),
        title: `Take ${tasks.length} task(s) off the plan and solve again?`,
        description: [
          ...tasks.map(c => `${c.title}  ${dayLabel(dayKey(c.start))} ${hhmm(c.start)}–${hhmm(c.end)}`),
          '',
          'They go back to the inbox and the solver places them around the calendar again. Nothing changes on a cloud calendar until it is pushed.'
        ].join('\n'),
        confirmLabel: 'Re-plan',
        busyLabel: 'Re-planning…',
        onConfirm: () => replan.mutateAsync()
      })
    ]
  })
}

const WEEKDAY_KEYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat']

/**
 * The user's week as the planner knows it: its own calendar (every block already on the plan),
 * the copies of the cloud calendars Hermes last pulled, and a pending proposal over them.
 */
function WeekView({ status }) {
  const [offset, setOffset] = useState(0)

  const monday = new Date()
  monday.setHours(12, 0, 0, 0)
  monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7) + 7 * offset)
  const week = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(monday)
    d.setDate(d.getDate() + i)
    return { key: isoDay(d), weekday: WEEKDAY_KEYS[d.getDay()] }
  })
  const data = useWeek(week[0].key, 7)
  const proposal = useProposal(null)
  const pending = proposal.data?.ok && !proposal.data.applied_at ? proposal.data : null
  const events = data.data?.events || []
  const clashes = data.data?.clashes || []
  const calendarsRead = status?.calendars || []
  const sources = calendarsRead.map(c => c.source)
  const push = status?.push
  const working = new Set(Object.keys(status?.availability || {}))
  const proposed = pending ? pending.scheduled.filter(b => week.some(d => d.key === dayKey(b.start))) : []
  // Weekend columns only when the user works then or something is on them.
  const days = week
    .filter(d => !working.size || working.has(d.weekday) || events.some(e => dayKey(e.start) === d.key) ||
      proposed.some(b => dayKey(b.start) === d.key))
    .map(d => d.key)
  // The planner reads and plans from today on: a past day is past, not unread.
  const today = isoDay(new Date())
  const unread = new Set(
    calendarsRead.length
      ? days.filter(d => d >= today && !calendarsRead.some(c => c.window[0] <= d && d < c.window[1]))
      : []
  )

  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 8 },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' },
        children: [
          jsx(Button, { size: 'xs', variant: 'ghost', onClick: () => setOffset(offset - 1), children: '‹' }),
          jsx(Button, { size: 'xs', variant: 'ghost', disabled: offset === 0, onClick: () => setOffset(0), children: 'This week' }),
          jsx(Button, { size: 'xs', variant: 'ghost', onClick: () => setOffset(offset + 1), children: '›' }),
          jsx('span', {
            style: { ...text('--ui-text-secondary', '0.75rem'), fontWeight: 500 },
            children: `${dayLabel(week[0].key)} – ${dayLabel(week[6].key)}`
          }),
          jsx('span', { style: { flex: 1 } }),
          jsx(CalendarChips, { calendars: calendarsRead, sources }),
          push && (push.create || push.delete)
            ? jsx(Tip, {
                label: `Ask Hermes to update ${push.calendar}: ${push.create} to add, ${push.delete} to remove. Only the planner's own events change.`,
                children: jsx(Badge, {
                  variant: 'warn',
                  children: `${push.create + push.delete} change(s) to push to ${push.calendar}`
                })
              })
            : null
        ]
      }),
      clashes.length > 0 &&
        jsxs('div', {
          'data-banner': 'clashes',
          style: {
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '6px 8px',
            borderRadius: 4,
            border: `1px solid ${RED}`,
            ...text('--ui-text-primary', '0.75rem')
          },
          children: [
            jsx('span', {
              style: { flex: 1 },
              children: clashes
                .map(c => `${c.title} clashes with ${c.with} · ${dayLabel(dayKey(c.start))} ${hhmm(c.with_start)}`)
                .join('; ')
            }),
            jsx(ReplanButton, { clashes })
          ]
        }),
      pending &&
        jsxs('div', {
          style: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
          children: [
            jsx('span', { style: { ...text('--ui-text-primary', '0.8rem'), fontWeight: 600 }, children: 'Proposal' }),
            jsx(ScoreBadge, { plan: pending }),
            jsx('span', {
              style: text('--ui-text-tertiary', '0.7rem'),
              children: `${pending.scheduled.length} placed${pending.unscheduled.length ? `, ${pending.unscheduled.length} not placed` : ''}${
                proposed.length < pending.scheduled.length ? ` · ${pending.scheduled.length - proposed.length} in other weeks` : ''
              }`
            }),
            jsx('span', { style: { flex: 1 } }),
            jsx(ApplyButton, { plan: pending })
          ]
        }),
      data.isLoading
        ? jsx(GlyphSpinner, {})
        : jsx(WeekGrid, {
            days,
            busy: events.filter(e => e.kind === 'busy'),
            applied: events.filter(e => e.kind === 'planned'),
            proposed,
            unread,
            sources,
            height: 460
          }),
      pending && jsx(Unscheduled, { plan: pending }),
      !events.length && !proposed.length && !data.isLoading &&
        jsx('div', {
          style: text('--ui-text-tertiary', '0.75rem'),
          children: calendarsRead.length
            ? 'Nothing on this week.'
            : 'No calendar pulled. Ask Hermes to pull your calendar, or tell it when you are busy.'
        }),
      status?.local_calendar &&
        jsx('span', {
          style: text('--ui-text-quaternary', '0.65rem'),
          children: `The planner's calendar: ${status.local_calendar}`
        })
    ]
  })
}

const durationLabel = m => (m < 60 ? `${m} min` : m % 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m / 60} h`)
const whenLabel = stamp => `${dayLabel(dayKey(stamp))} ${hhmm(stamp)}`
const nowStamp = () => {
  const now = new Date()
  return `${isoDay(now)} ${now.toTimeString().slice(0, 8)}`
}

function Section({ title, count, note, children, empty }) {
  return jsxs('section', {
    'data-section': title,
    style: { display: 'flex', flexDirection: 'column', gap: 2 },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'baseline', gap: 8, padding: '0 8px 4px' },
        children: [
          jsx('span', {
            style: { ...text('--ui-text-secondary', '0.7rem'), fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase' },
            children: title
          }),
          jsx('span', { style: text('--ui-text-quaternary', '0.7rem'), children: count }),
          note && jsx('span', { style: { ...text('--ui-text-quaternary', '0.7rem'), marginLeft: 'auto' }, children: note })
        ]
      }),
      count ? children : jsx('div', { style: { ...text('--ui-text-tertiary', '0.75rem'), padding: '2px 8px 6px' }, children: empty })
    ]
  })
}

function TaskRow({ when, title, meta, muted, accent }) {
  return jsxs('div', {
    style: {
      display: 'grid',
      gridTemplateColumns: '7.5rem 1fr',
      gap: 12,
      alignItems: 'baseline',
      padding: '6px 8px',
      borderRadius: 4,
      borderLeft: `2px solid ${accent ? 'var(--ui-accent)' : 'transparent'}`,
      opacity: muted ? 0.6 : 1
    },
    children: [
      jsx('span', { style: { ...text('--ui-text-tertiary', '0.72rem'), fontVariantNumeric: 'tabular-nums' }, children: when }),
      jsxs('span', {
        style: { display: 'flex', flexDirection: 'column', gap: 1, minWidth: 0 },
        children: [
          jsx('span', { style: { ...text('--ui-text-primary', '0.8rem'), }, children: title }),
          meta && jsx('span', { style: text('--ui-text-tertiary', '0.7rem'), children: meta })
        ]
      })
    ]
  })
}

/**
 * The tasks, by where they stand: waiting for a plan, on the planner's calendar, or past (the block
 * has ended; whether the work got done is not the planner's to say). Events deleted on a cloud
 * calendar reach the planner at Hermes's next pull, so the calendar section says how old its copy is.
 */
export function TasksView({ status, inbox }) {
  const [showPast, setShowPast] = useState(false)
  const tasks = inbox?.tasks || []
  const after = new Map()
  for (const rule of inbox?.order_rules || []) {
    after.set(rule.after_id, [...(after.get(rule.after_id) || []), rule.before])
  }
  const stamp = nowStamp()
  const waiting = tasks.filter(t => t.state === 'inbox')
  const placed = tasks.filter(t => t.state === 'applied' && t.block).sort((a, b) => a.block.start.localeCompare(b.block.start))
  const upcoming = placed.filter(t => t.block.end > stamp)
  const past = placed.filter(t => t.block.end <= stamp).reverse()
  const calendars = status?.calendars || []
  const pulled = calendars.length ? calendars.map(c => `${c.source} · pulled ${ageOf(c.read_at)}`).join('  ') : null
  const today = isoDay(new Date())
  const tomorrow = (() => {
    const d = new Date()
    d.setDate(d.getDate() + 1)
    return isoDay(d)
  })()
  const dayName = key => (key === today ? 'Today' : key === tomorrow ? 'Tomorrow' : dayLabel(key))
  const byDay = [...new Set(upcoming.map(t => dayKey(t.block.start)))]

  // What still shapes a task's placement. Once placed, only its length, load and deadline matter.
  const details = (t, placed) =>
    [
      placed && durationLabel(t.duration_minutes),
      t.priority === 'high' && 'high priority',
      t.priority === 'low' && 'low priority',
      t.cognitive_load === 'high' && 'deep work',
      t.deadline_at && `${t.deadline_kind === 'hard' ? 'due' : 'ideally by'} ${whenLabel(t.deadline_at)}`,
      !placed && t.earliest_at && `not before ${whenLabel(t.earliest_at)}`,
      !placed && after.has(t.id) && `after ${after.get(t.id).join(', ')}`
    ]
      .filter(Boolean)
      .join(' · ')

  if (!tasks.length) {
    return jsx(EmptyState, { title: 'No tasks yet', description: 'Tell Hermes what you need to get done this week.' })
  }

  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 18, maxWidth: 760 },
    children: [
      jsx(Section, {
        title: 'To plan',
        count: waiting.length,
        empty: 'Nothing waiting. New tasks land here until a plan places them.',
        children: waiting.map(t => jsx(TaskRow, { when: durationLabel(t.duration_minutes), title: t.title, meta: details(t, false) }, t.id))
      }),
      jsx(Section, {
        title: 'On your calendar',
        count: upcoming.length,
        note: pulled,
        empty: 'No upcoming blocks. Apply a plan to put tasks here.',
        children: byDay.map(key =>
          jsxs(
            'div',
            {
              style: { display: 'flex', flexDirection: 'column', gap: 2 },
              children: [
                jsx('div', {
                  style: { ...text(key === today ? '--ui-accent' : '--ui-text-secondary', '0.72rem'), fontWeight: 500, padding: '6px 8px 0' },
                  children: dayName(key)
                }),
                ...upcoming
                  .filter(t => dayKey(t.block.start) === key)
                  .map(t =>
                    jsx(
                      TaskRow,
                      {
                        when: `${hhmm(t.block.start)}–${hhmm(t.block.end)}`,
                        title: t.title,
                        meta: details(t, true),
                        accent: t.block.start <= stamp
                      },
                      t.id
                    )
                  )
              ]
            },
            key
          )
        )
      }),
      past.length > 0 &&
        jsxs('section', {
          'data-section': 'Past',
          style: { display: 'flex', flexDirection: 'column', gap: 2 },
          children: [
            jsx(Button, {
              size: 'xs',
              variant: 'ghost',
              onClick: () => setShowPast(!showPast),
              children: `${showPast ? '▾' : '▸'} Past · ${past.length}`
            }),
            showPast &&
              past.map(t =>
                jsx(TaskRow, { when: dayLabel(dayKey(t.block.start)), title: t.title, meta: `${hhmm(t.block.start)}–${hhmm(t.block.end)}`, muted: true }, t.id)
              )
          ]
        })
    ]
  })
}

function PlannerPage() {
  const [tab, setTab] = useState('week')
  const client = useQueryClient()
  const status = useQuery({ queryKey: qk('status'), queryFn: () => rest('/status'), staleTime: 10_000 })
  const inbox = useQuery({ queryKey: qk('inbox'), queryFn: () => rest('/inbox?state=all'), staleTime: 5_000 })
  const solve = useMutation({
    mutationFn: () => rest('/optimize', { method: 'POST', body: {}, timeoutMs: 180_000 }),
    onSuccess: result => {
      client.invalidateQueries({ queryKey: [ID] })
      if (!result?.ok) {
        host.notify({ kind: 'error', message: `Planner: ${plainError(result, 'solve failed')}` })
      }
    }
  })
  const ready = status.data?.ok && status.data.ready

  const body = () => {
    if (status.data && !status.data.ok) {
      return jsx(EmptyState, { title: 'Planner unavailable', description: status.data.error })
    }
    if (status.data && !status.data.engine?.path) {
      return jsx(PanelEmpty, {
        icon: 'cloud-download',
        title: 'Install the SolverForge engine',
        description: 'One download from this plugin’s GitHub release, checked against a pinned SHA-256.',
        action: jsx(InstallButton, {})
      })
    }
    if (status.data && !ready) {
      return jsx(EmptyState, {
        title: 'Planner needs setup',
        description: `${status.data.problems.join(' · ')}. Tell Hermes your timezone and working hours.`
      })
    }
    if (tab === 'inbox') {
      return jsx(TasksView, { status: status.data, inbox: inbox.data })
    }
    return jsx(WeekView, { status: status.data })
  }

  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 12, padding: 16, height: '100%', overflow: 'auto' },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', gap: 10 },
        children: [
          jsx('span', { style: { ...text('--ui-text-primary', '0.95rem'), fontWeight: 600 }, children: 'Planner' }),
          jsx(SegmentedControl, {
            value: tab,
            onChange: setTab,
            options: [
              { id: 'week', label: 'Week' },
              { id: 'inbox', label: 'Tasks' }
            ]
          }),
          jsx('span', { style: { flex: 1 } }),
          status.data?.timezone && jsx('span', { style: text('--ui-text-quaternary', '0.7rem'), children: status.data.timezone }),
          jsx(Button, {
            size: 'xs',
            variant: 'outline',
            disabled: !ready || solve.isPending,
            onClick: () => solve.mutate(),
            children: solve.isPending ? 'Solving…' : 'Solve now'
          })
        ]
      }),
      body()
    ]
  })
}

function StatusChip() {
  const query = useProposal(null)
  const plan = query.data?.ok ? query.data : null
  if (!plan) {
    return null
  }
  const pending = plan.can_apply && !plan.applied_at && plan.scheduled.length
  const stamp = nowStamp()
  const next = plan.applied_at ? plan.scheduled.find(b => b.end > stamp) : null
  const label = pending ? `▦ ${plan.scheduled.length} to approve` : next ? `▦ ${hhmm(next.start)} ${next.title}` : null
  if (!label) {
    return null
  }
  return jsx(Tip, {
    label: pending ? `Proposal ${plan.score} is waiting for approval` : `Next planned block: ${next.day}`,
    children: jsx('button', {
      type: 'button',
      onClick: () => host.navigate(ROUTE),
      style: { ...text(pending ? '--ui-accent' : '--ui-text-tertiary', '0.6875rem'), padding: '0 6px', height: '100%' },
      children: label
    })
  })
}

export default {
  id: ID,
  name: 'SolverForge Planner',
  description: 'Constraint-solved plans: transcript plan cards, a Planner page and a status chip.',
  register(ctx) {
    rest = (path, opts) => ctx.rest(path, opts)

    // Tools broadcast when the plan changes, so every open card and page refreshes at once.
    const refresh = () => queryClient.invalidateQueries({ queryKey: [ID] })
    ctx.onEvent(`plugin.${ID}.changed`, refresh)

    ctx.registerMany([
      {
        id: 'plan-card',
        area: TRANSCRIPT_DIRECTIVE_AREA,
        data: { name: 'planner', render: props => jsx(PlanCard, props) }
      },
      { id: 'page', area: ROUTES_AREA, title: 'Planner', data: { path: ROUTE }, render: () => jsx(PlannerPage, {}) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: ROUTE, label: 'Planner', codicon: 'calendar' } },
      { id: 'chip', area: STATUSBAR_AREAS.right, order: 125, render: () => jsx(StatusChip, {}) },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: { id: 'solverforge-planner.open', label: 'Planner: open', keywords: ['plan', 'calendar', 'schedule'], run: () => host.navigate(ROUTE) }
      },
      {
        id: 'solve',
        area: PALETTE_AREA,
        data: {
          id: 'solverforge-planner.solve',
          label: 'Planner: solve now',
          keywords: ['optimize', 'schedule', 'replan'],
          run: () =>
            rest('/optimize', { method: 'POST', body: {}, timeoutMs: 180_000 }).then(result => {
              refresh()
              host.notify(
                result?.ok
                  ? { kind: 'success', message: `Planner: proposal ${result.score}` }
                  : { kind: 'error', message: `Planner: ${plainError(result, 'solve failed')}` }
              )
              host.navigate(ROUTE)
            })
        }
      }
    ])
  }
}
