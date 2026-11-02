import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { router } from './router'
import './styles/theme.css'

// Hash addresses from before the rebuild keep working.
const legacy: Record<string, string> = { '#check': '/', '#credibility': '/', '#scoring': '/', '#reports': '/reports' }
if (legacy[window.location.hash]) window.history.replaceState(null, '', legacy[window.location.hash])

const queryClient = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, staleTime: 5_000 } } })

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
