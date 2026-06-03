import { Sidebar } from '@/components/Sidebar'
import { SmartAssistant } from '@/components/SmartAssistant'
import { DashboardTopBar } from '@/components/DashboardTopBar'
import { CopilotBar } from '@/components/CopilotBar'
import { ErrorBoundary } from '@/components/system/ErrorBoundary'

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col sm:flex-row">
      <ErrorBoundary name="Sidebar">
        <Sidebar />
      </ErrorBoundary>
      <div className="flex-1 min-w-0 overflow-x-hidden flex flex-col">
        <ErrorBoundary name="DashboardTopBar">
          <DashboardTopBar />
        </ErrorBoundary>
        <ErrorBoundary name="CopilotBar">
          <CopilotBar />
        </ErrorBoundary>
        <ErrorBoundary name="DashboardPage">
          {children}
        </ErrorBoundary>
      </div>
      <ErrorBoundary name="SmartAssistant">
        <SmartAssistant />
      </ErrorBoundary>
    </div>
  )
}
