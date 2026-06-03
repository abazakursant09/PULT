import { Sidebar } from '@/components/Sidebar'
import { DashboardTopBar } from '@/components/DashboardTopBar'

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex" style={{ minHeight: '100vh', background: '#09090B' }}>
      <Sidebar />
      <div className="flex-1 min-w-0 flex flex-col" style={{ minHeight: '100vh' }}>
        <DashboardTopBar />
        <main className="flex-1" style={{ background: '#09090B' }}>
          {children}
        </main>
      </div>
    </div>
  )
}
