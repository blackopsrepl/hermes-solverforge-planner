// A stand-in for the Desktop SDK: components render as plain elements that keep their text,
// and queries are answered from the fixture the test sets. Only what plugin.js imports.
import { createElement as h } from 'react'

let fixture = {}
export const submitted = []
export const setFixture = value => {
  fixture = value
  submitted.length = 0
}

const box = name => ({ children, label, title, description, variant, action }) =>
  h('div', { 'data-c': name, 'data-variant': variant }, title, description, label && h('i', null, label), action, children)

export const Badge = box('Badge')
export const EmptyState = box('EmptyState')
export const PanelEmpty = box('PanelEmpty')
export const GlyphSpinner = box('Spinner')
export const Button = ({ children, disabled }) => h('button', { disabled }, children)
export const Tip = ({ label, children }) => h('span', { title: label }, children)
export const ConfirmDialog = () => null
export const SegmentedControl = ({ options, value }) =>
  h('nav', null, options.map(o => h('b', { key: o.id, 'data-on': o.id === value }, o.label)))

export const PALETTE_AREA = 'palette'
export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar'
export const TRANSCRIPT_DIRECTIVE_AREA = 'transcript'
export const STATUSBAR_AREAS = { right: 'statusbar-right' }

export const queryClient = { invalidateQueries() {} }
export const useQueryClient = () => queryClient
export const useMutation = () => ({ mutate() {}, mutateAsync: async () => {}, isPending: false })
export const host = {
  notify() {},
  navigate() {},
  composer: { submit: (_, text) => submitted.push(text) > 0 }
}

export const useQuery = ({ queryKey }) => {
  const [, kind, ...rest] = queryKey
  if (kind === 'week') {
    return { data: fixture.weeks?.[rest[0]] ?? { ok: true, events: [] }, isLoading: false }
  }
  return { data: fixture[kind], isLoading: false }
}
