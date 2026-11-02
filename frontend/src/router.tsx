import { createRootRoute, createRoute, createRouter, lazyRouteComponent } from '@tanstack/react-router'
import { NotFound, PageError } from './app/Fallbacks'
import { Shell } from './app/Shell'
import { CheckPage } from './features/check/CheckPage'
import { HomePage } from './features/home/HomePage'
import { reducedMotion } from './ui/motion'

const root = createRootRoute({ component: Shell, notFoundComponent: NotFound })
const home = createRoute({ getParentRoute: () => root, path: '/', component: HomePage })
// ?view=report opens the report as a document instead of the paper with its widgets.
const check = createRoute({
  getParentRoute: () => root, path: '/check/$id', component: CheckPage,
  // ?part=quality shows the quality assessment of the same run; the two parts are peers.
  validateSearch: (search: Record<string, unknown>): { view?: 'report'; part?: 'quality' } => ({
    ...(search.view === 'report' ? { view: 'report' as const } : {}), ...(search.part === 'quality' ? { part: 'quality' as const } : {}),
  }),
})
// Pages off the main path load on demand, which keeps the first download small.
const print = createRoute({ getParentRoute: () => root, path: '/check/$id/print', component: lazyRouteComponent(() => import('./features/report/PrintReport'), 'PrintReport') })
const reports = createRoute({ getParentRoute: () => root, path: '/reports', component: lazyRouteComponent(() => import('./features/reports/ReportsPage'), 'ReportsPage') })
const score = createRoute({ getParentRoute: () => root, path: '/score/$id', component: lazyRouteComponent(() => import('./features/score/ScorePage'), 'ScorePage') })

/**
 * Every report has its own address; navigation zooms between pages where the browser supports it.
 * No transition on the first load, in a background tab (the browser would hold the render until the
 * tab is shown) or with reduced motion.
 */
export const router = createRouter({
  routeTree: root.addChildren([home, check, print, reports, score]),
  defaultViewTransition: {
    types: ({ fromLocation }) => (fromLocation && document.visibilityState === 'visible' && !reducedMotion() ? [] : false),
  },
  scrollRestoration: true,
  defaultNotFoundComponent: NotFound,
  defaultErrorComponent: PageError,
})

declare module '@tanstack/react-router' {
  interface Register { router: typeof router }
}
