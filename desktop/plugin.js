/**
 * SolverForge Planner — Desktop half.
 *
 * Surfaces: the ::planner{proposal="…"} plan card in the transcript, a Planner
 * page with its sidebar row, a status-bar chip, palette commands and live
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
        host.notify({ kind: 'success', message: `Planner: ${result.written.length} block(s) on the calendar` })
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

/** Days covered by the blocks, in order, padded so a single day still reads as a day. */
function daysOf(blocks) {
  return [...new Set(blocks.map(b => dayKey(b.start)))].sort()
}

/** A compact day-column grid: busy time muted, proposed blocks in the accent. */
function WeekGrid({ blocks, busy, height = 240 }) {
  const days = daysOf(blocks)
  if (!days.length) {
    return null
  }
  const all = [...blocks, ...busy.filter(b => days.includes(dayKey(b.start)) && !b.all_day)]
  const lo = Math.min(DAY_START * 60, ...all.map(b => minutes(b.start)))
  const hi = Math.max(DAY_END * 60, ...all.map(b => (dayKey(b.end) > dayKey(b.start) ? 1440 : minutes(b.end))))
  const span = Math.max(60, hi - lo)
  const y = m => `${((m - lo) / span) * 100}%`
  // Blocks keep their true length so back-to-back blocks never overlap; a block
  // too short for a line of text drops its label and keeps it in the tooltip.
  const labelFits = (start, end) => ((end - start) / span) * height >= 13

  const place = (b, kind) => {
    const top = minutes(b.start)
    const bottom = dayKey(b.end) > dayKey(b.start) ? 1440 : minutes(b.end)
    const planned = kind === 'plan'
    return jsx(
      Tip,
      {
        label: `${b.title}  ${hhmm(b.start)}–${hhmm(b.end)}${planned ? '' : ' (busy)'}`,
        children: jsx('div', {
          style: {
            position: 'absolute',
            left: planned ? 2 : 0,
            right: planned ? 2 : 0,
            top: `calc(${y(top)} + 1px)`,
            height: `max(3px, calc(${y(bottom)} - ${y(top)} - 2px))`,
            borderRadius: 3,
            overflow: 'hidden',
            padding: '1px 4px',
            background: planned
              ? 'color-mix(in srgb, var(--ui-accent) 22%, transparent)'
              : 'color-mix(in srgb, var(--ui-text-quaternary) 18%, transparent)',
            borderLeft: `2px solid var(${planned ? '--ui-accent' : '--ui-stroke-secondary'})`,
            ...text(planned ? '--ui-text-primary' : '--ui-text-tertiary', '0.65rem'),
            lineHeight: 1.25,
            zIndex: planned ? 2 : 1
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

  return jsxs('div', {
    style: { display: 'grid', gridTemplateColumns: `2.25rem repeat(${days.length}, minmax(0, 1fr))`, gap: 4 },
    children: [
      jsx('div', {}),
      ...days.map(d =>
        jsx('div', { style: { ...text('--ui-text-secondary', '0.7rem'), fontWeight: 500 }, children: dayLabel(d) }, `h-${d}`)
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
              background: 'color-mix(in srgb, var(--ui-stroke-tertiary) 25%, transparent)'
            },
            children: [
              ...busy.filter(b => dayKey(b.start) === d && !b.all_day).map(b => place(b, 'busy')),
              ...blocks.filter(b => dayKey(b.start) === d).map(b => place(b, 'plan'))
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
        title: `Put ${plan.scheduled.length} block(s) on your calendar?`,
        description: plan.scheduled.map(b => `${b.day} ${hhmm(b.start)}–${hhmm(b.end)}  ${b.title}`).join('\n'),
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
        ? jsx(WeekGrid, { blocks: plan.scheduled, busy: busyEvents, height: compact ? 300 : 460 })
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

function PlannerPage() {
  const [tab, setTab] = useState('plan')
  const client = useQueryClient()
  const proposal = useProposal(null)
  const status = useQuery({ queryKey: qk('status'), queryFn: () => rest('/status'), staleTime: 10_000 })
  const inbox = useQuery({ queryKey: qk('inbox'), queryFn: () => rest('/inbox?state=all'), staleTime: 5_000 })
  const solve = useMutation({
    mutationFn: () => rest('/optimize', { method: 'POST', body: {}, timeoutMs: 180_000 }),
    onSuccess: result => {
      client.invalidateQueries({ queryKey: [ID] })
      if (!result?.ok) {
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
    if (proposal.isLoading) {
      return jsx(GlyphSpinner, {})
    }
    return proposal.data?.ok
      ? jsx(PlanView, { plan: proposal.data })
      : jsx(EmptyState, { title: 'No proposal yet', description: 'Solve to create one.' })
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
              { id: 'plan', label: 'Proposal' },
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
