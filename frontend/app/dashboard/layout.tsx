import '../../styles/seller.css'
import { Rail } from '@/components/seller/Shell'
import { ErrorBoundary } from '@/components/system/ErrorBoundary'

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="s-app">
      <ErrorBoundary name="Rail"><Rail /></ErrorBoundary>
      <main className="s-main">
        <ErrorBoundary name="DashboardPage">{children}</ErrorBoundary>
      </main>
    </div>
  )
}
