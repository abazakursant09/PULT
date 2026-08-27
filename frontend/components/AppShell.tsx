import { Rail, NavProvider, ShellFrame, SellerBar } from '@/components/seller/Shell'
import { ErrorBoundary } from '@/components/system/ErrorBoundary'

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <NavProvider>
      <ShellFrame>
        <ErrorBoundary name="Rail"><Rail /></ErrorBoundary>
        <main className="s-main">
          <SellerBar title="Пульт" sub="Рабочий контур" />
          <ErrorBoundary name="LegacyToolPage">{children}</ErrorBoundary>
        </main>
      </ShellFrame>
    </NavProvider>
  )
}
