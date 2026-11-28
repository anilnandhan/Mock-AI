import { useEffect, useState } from 'react'
import Home from './pages/Home'
import Interview from './pages/Interview'
import Report from './pages/Report'

type Route = { page: 'home' } | { page: 'interview'; id: string } | { page: 'report'; id: string }

function parseHash(): Route {
  const [, page, id] = window.location.hash.replace(/^#/, '').split('/')
  if (page === 'interview' && id) return { page: 'interview', id }
  if (page === 'report' && id) return { page: 'report', id }
  return { page: 'home' }
}

export function navigate(path: string) {
  window.location.hash = path
}

export default function App() {
  const [route, setRoute] = useState<Route>(parseHash)

  useEffect(() => {
    const onHash = () => setRoute(parseHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  if (route.page === 'interview') return <Interview key={route.id} sessionId={route.id} />
  if (route.page === 'report') return <Report key={route.id} sessionId={route.id} />
  return <Home />
}
