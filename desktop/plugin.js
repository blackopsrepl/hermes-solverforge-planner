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

// Only the agent has a calendar tool. When a plan or an apply needs the user's calendars
// read, or planned events created there, the request goes to the chat instead.
const NEEDS_AGENT = new Set(['calendar_stale'])

function askHermes(prompt) {
  const sent = host.composer?.submit?.(null, prompt)
  if (!sent) {
    host.notify({ kind: 'info', message: `Planner: ask Hermes in a chat: “${prompt}”` })
  }
  return sent
}

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
    onSuccess: (result, id) => {
      client.invalidateQueries({ queryKey: [ID] })
      if (result?.ok && result.create_on_calendar?.length) {
        askHermes(`Put the applied planner proposal ${id} on my calendar`)
      } else if (result?.ok) {
        host.notify({ kind: 'success', message: `Planner: ${result.written.length} block(s) applied` })
      } else if (NEEDS_AGENT.has(result?.code)) {
        askHermes(`Read my calendars and apply planner proposal ${id}`)
      } else {
        host.notify({ kind: 'error', message: `Planner: ${result?.error || 'apply refused'}` })
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
  busy: { suffix: ' (busy)', fill: '--ui-text-quaternary', mix: 18, edge: '--ui-stroke-secondary', ink: '--ui-text-tertiary', z: 1 },
  applied: { suffix: ' (on your plan)', fill: '--ui-accent', mix: 12, edge: '--ui-accent', ink: '--ui-text-secondary', z: 2 },
  proposed: { suffix: ' (proposed)', fill: '--ui-accent', mix: 26, edge: '--ui-accent', ink: '--ui-text-primary', z: 3, dashed: true }
}

/**
 * A compact day-column grid: busy time muted, blocks already on the plan in a light accent,
 * proposed blocks in the full accent with a dashed edge. Days in ``unread`` are outside every
 * calendar read and are hatched, so an empty day never passes for a free one.
 */
function WeekGrid({ days, busy = [], applied = [], proposed = [], unread = new Set(), height = 240 }) {
  if (!days.length) {
    return null
  }
  const all = [...busy, ...applied, ...proposed].filter(b => days.includes(dayKey(b.start)))
  const lo = Math.min(DAY_START * 60, ...all.map(b => minutes(b.start)))
  const hi = Math.max(DAY_END * 60, ...all.map(b => (dayKey(b.end) > dayKey(b.start) ? 1440 : minutes(b.end))))
  const span = Math.max(60, hi - lo)
  const y = m => `${((Math.max(lo, m) - lo) / span) * 100}%`
  // Blocks keep their true length so back-to-back blocks never overlap; a block
  // too short for a line of text drops its label and keeps it in the tooltip.
  const labelFits = (start, end) => ((end - start) / span) * height >= 13

  const place = (b, kind) => {
    const k = KINDS[kind]
    const top = minutes(b.start)
    const bottom = dayKey(b.end) > dayKey(b.start) ? 1440 : minutes(b.end)
    const inset = kind === 'busy' ? 0 : 2
    return jsx(
      Tip,
      {
        label: `${b.title}  ${hhmm(b.start)}–${hhmm(b.end)}${k.suffix}`,
        children: jsx('div', {
          style: {
            position: 'absolute',
            left: inset,
            right: inset,
            top: `calc(${y(top)} + 1px)`,
            height: `max(3px, calc(${y(bottom)} - ${y(top)} - 2px))`,
            borderRadius: 3,
            overflow: 'hidden',
            padding: '1px 4px',
            background: `color-mix(in srgb, var(${k.fill}) ${k.mix}%, transparent)`,
            borderLeft: `2px solid var(${k.edge})`,
            outline: k.dashed ? '1px dashed var(--ui-accent)' : undefined,
            outlineOffset: -1,
            ...text(k.ink, '0.65rem'),
            lineHeight: 1.25,
            zIndex: k.z
          },
          children: labelFits(top, bottom) ? `${hhmm(b.start)} ${b.title}` : null
        })
      },
      `${kind}-${b.start}-${b.title}`
    )
  }

  const hours = []
  for (let h = Math.ceil(lo / 60); h * 60 <= hi; h += 2) {
    hours.push(h)
  }
  const today = isoDay(new Date())

  return jsxs('div', {
    style: { display: 'grid', gridTemplateColumns: `2.25rem repeat(${days.length}, minmax(0, 1fr))`, gap: 4 },
    children: [
      jsx('div', {}),
      ...days.map(d =>
        jsxs(
          'div',
          {
            style: { ...text(d === today ? '--ui-accent' : '--ui-text-secondary', '0.7rem'), fontWeight: 500 },
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
        jsxs(
          'div',
          {
            style: {
              position: 'relative',
              height,
              borderRadius: 4,
              background: unread.has(d)
                ? 'repeating-linear-gradient(135deg, color-mix(in srgb, var(--ui-stroke-tertiary) 30%, transparent) 0 6px, transparent 6px 12px)'
                : 'color-mix(in srgb, var(--ui-stroke-tertiary) 25%, transparent)'
            },
            children: [
              ...busy.filter(b => dayKey(b.start) === d).map(b => place(b, 'busy')),
              ...applied.filter(b => dayKey(b.start) === d).map(b => place(b, 'applied')),
              ...proposed.filter(b => dayKey(b.start) === d).map(b => place(b, 'proposed'))
            ]
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
        title: plan.publish_to
          ? `Add ${plan.scheduled.length} event(s) to your “${plan.publish_to}” calendar?`
          : `Apply ${plan.scheduled.length} block(s)?`,
        description: [
          ...plan.scheduled.map(b => `${b.day} ${hhmm(b.start)}–${hhmm(b.end)}  ${b.title}`),
          '',
          plan.calendars?.length
            ? 'Hermes reads your calendars again first, then adds the events. Existing events are not changed.'
            : 'Existing events are not changed.'
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

const WEEKDAY_KEYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat']

/**
 * The user's week as the planner knows it: their calendar from the last read, every block already
 * on their plan, and a pending proposal over them. The planner cannot read a calendar itself, so
 * Refresh asks Hermes to; the read time says how old the picture is.
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
  const calendars = status?.calendars || []
  const working = new Set(Object.keys(status?.availability || {}))
  const proposed = pending ? pending.scheduled.filter(b => week.some(d => d.key === dayKey(b.start))) : []
  // Weekend columns only when the user works then or something is on them.
  const days = week
    .filter(d => !working.size || working.has(d.weekday) || events.some(e => dayKey(e.start) === d.key) ||
      proposed.some(b => dayKey(b.start) === d.key))
    .map(d => d.key)
  const unread = new Set(
    calendars.length ? days.filter(d => !calendars.some(c => c.window[0] <= d && d < c.window[1])) : []
  )
  const oldest = calendars.map(c => c.read_at).sort()[0]
  const sunday = week[6].key

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
            children: `${dayLabel(week[0].key)} – ${dayLabel(sunday)}`
          }),
          jsx('span', { style: { flex: 1 } }),
          calendars.length
            ? jsx('span', {
                style: text('--ui-text-quaternary', '0.7rem'),
                children: `Calendar read ${oldest.slice(8, 10)}/${oldest.slice(5, 7)} ${oldest.slice(11, 16)}`
              })
            : null,
          calendars.length
            ? jsx(Button, {
                size: 'xs',
                variant: 'outline',
                onClick: () => askHermes('Read my calendars for the planner'),
                children: 'Refresh'
              })
            : null
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
            height: 460
          }),
      pending && jsx(Unscheduled, { plan: pending }),
      !events.length && !proposed.length && !data.isLoading &&
        jsx('div', {
          style: text('--ui-text-tertiary', '0.75rem'),
          children: calendars.length
            ? 'Nothing on this week.'
            : 'No calendar read yet. Ask Hermes to read your calendar, or to plan something.'
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
      if (NEEDS_AGENT.has(result?.code)) {
        askHermes('Read my calendars and plan my planner inbox')
      } else if (!result?.ok) {
        host.notify({ kind: 'error', message: `Planner: ${result?.error}` })
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
      const tasks = inbox.data?.tasks || []
      return tasks.length
        ? jsx('div', {
            style: { display: 'flex', flexDirection: 'column', gap: 4 },
            children: tasks.map(t =>
              jsxs(
                'div',
                {
                  style: { display: 'flex', gap: 8, alignItems: 'baseline' },
                  children: [
                    jsx(Badge, { variant: t.state === 'applied' ? 'muted' : 'outline', size: 'xs', children: t.state }),
                    jsx('span', { style: text('--ui-text-primary', '0.8rem'), children: t.title }),
                    jsx('span', {
                      style: text('--ui-text-tertiary', '0.7rem'),
                      children: [
                        `${t.duration_minutes} min`,
                        t.priority,
                        t.cognitive_load && `${t.cognitive_load} load`,
                        t.deadline_at && `${t.deadline_kind} deadline ${t.deadline_at.slice(0, 16)}`
                      ]
                        .filter(Boolean)
                        .join(' · ')
                    })
                  ]
                },
                t.id
              )
            )
          })
        : jsx(EmptyState, { title: 'No tasks', description: 'Ask Hermes to plan something.' })
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
          jsx(CalendarsBadge, { status: status.data }),
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

/** Which calendars plans go around, when each was read, and planned events not there yet. */
function CalendarsBadge({ status }) {
  if (!status?.ok) {
    return null
  }
  const calendars = status.calendars || []
  if (!calendars.length) {
    return jsx(Tip, {
      label: 'Plans only avoid busy time you tell Hermes about. Ask Hermes to read your calendar before planning.',
      children: jsx(Badge, { variant: 'warn', children: 'No calendar read' })
    })
  }
  const missing = status.not_on_calendar_yet || []
  const lines = [
    ...calendars.map(c => `${c.source}: read ${c.read_at.slice(5, 16)}`),
    status.publish_to ? `Planned blocks go to “${status.publish_to}”.` : 'No calendar chosen for planned blocks yet.',
    ...(missing.length ? [`${missing.length} planned event(s) not on the calendar yet.`] : [])
  ]
  return jsx(Tip, {
    label: lines.join('\n'),
    children: jsx(Badge, {
      variant: missing.length ? 'warn' : 'muted',
      children: `${calendars.length} calendar${calendars.length === 1 ? '' : 's'}${missing.length ? ` · ${missing.length} to add` : ''}`
    })
  })
}

function StatusChip() {
  const query = useProposal(null)
  const plan = query.data?.ok ? query.data : null
  if (!plan) {
    return null
  }
  const pending = plan.can_apply && !plan.applied_at && plan.scheduled.length
  const now = new Date()
  const stamp = `${now.toISOString().slice(0, 10)} ${now.toTimeString().slice(0, 5)}`
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
              if (NEEDS_AGENT.has(result?.code)) {
                askHermes('Read my calendars and plan my planner inbox')
                return
              }
              host.notify(
                result?.ok
                  ? { kind: 'success', message: `Planner: proposal ${result.score}` }
                  : { kind: 'error', message: `Planner: ${result?.error}` }
              )
              host.navigate(ROUTE)
            })
        }
      }
    ])
  }
}
