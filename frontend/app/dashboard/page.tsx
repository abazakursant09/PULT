'use client'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { SellerBar } from '@/components/seller/Shell'
import DecisionFeedPanel from '@/components/decision-feed/DecisionFeedPanel'
import TodayFocus from '@/components/decision-feed/TodayFocus'
import BusinessToday from '@/components/dashboard/BusinessToday'
import { EmptyState } from '@/components/ui/EmptyState'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'

const _WEEKDAYS = ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота']
const _MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
  'августа', 'сентября', 'октября', 'ноября', 'декабря']

function _greeting(hour: number): string {
  if (hour < 12) return 'доброе утро'
  if (hour < 18) return 'добрый день'
  return 'добрый вечер'
}

function _buildSub(now: Date): string {
  const weekday = _WEEKDAYS[now.getDay()]
  const date = `${now.getDate()} ${_MONTHS[now.getMonth()]}`
  return `${weekday}, ${date} · ${_greeting(now.getHours())}`
}

export default function Home() {
  const router = useRouter()
  // Compute in an effect (client-only) so real local date/time is used without an
  // SSR/CSR hydration mismatch.
  const [sub, setSub] = useState('')

  // The first-run gate used to read ONLY /api/today/summary.has_data — which is about the
  // most recent day's MONEY, not about whether a diagnosis exists. So a seller could upload a
  // report, PULT could diagnose a revenue collapse, /api/presentation/cards could return that
  // card, and the dashboard would still show "Нет данных для анализа". The same summary said
  // critical_count: 1 in the very response that claimed there was no data.
  //
  // The rule is now the honest one: if there is a diagnosis, the seller sees it. `has_data`
  // may only decide what to show when there is NOTHING to show.
  //
  // null = still loading. Both requests must settle before anything is drawn, so the
  // first-run screen can never flash at a seller who does in fact have a diagnosis.
  const [hasCards, setHasCards] = useState<boolean | null>(null)
  const [hasData, setHasData] = useState<boolean | null>(null)

  useEffect(() => { if (!localStorage.getItem('token')) router.push('/login') }, [router])
  useEffect(() => { setSub(_buildSub(new Date())) }, [])
  useEffect(() => {
    let alive = true

    api.today.getSummary()
      // Fail OPEN, as before: a transient summary error must never trap an active seller
      // behind the first-run screen.
      .then((r) => { if (alive) setHasData(r.has_data) })
      .catch(() => { if (alive) setHasData(true) })

    api.presentation.getCards({ limit: 1 })
      .then((r) => { if (alive) setHasCards(r.cards.length > 0) })
      // Fail CLOSED on this one: if we cannot fetch the cards we do not know whether a
      // diagnosis exists, and inventing one would be worse than deferring to `has_data`.
      .catch(() => { if (alive) setHasCards(false) })

    return () => { alive = false }
  }, [])

  const loading = hasCards === null || hasData === null
  // A diagnosis outranks everything. Only when there is none does `has_data` get a say.
  const showDiagnosis = !loading && (hasCards === true || hasData === true)
  const showFirstRun = !loading && !showDiagnosis

  return (
    <>
      <SellerBar title="Главная" sub={sub} />
      <div className="s-canvas">
        <BusinessToday />

        {/* a diagnosis exists (or the seller has recent data) → the normal dashboard */}
        {showDiagnosis && (
          <>
            <TodayFocus />
            <DecisionFeedPanel skipTopAction />
          </>
        )}

        {/* No data yet → send the seller down the ONE road that exists.
            This screen used to say "PULT подключён и ждёт данные с маркетплейса" and link to
            /dashboard/settings — a Telegram notifications page. Nothing about that was true:
            PULT has no marketplace connection UI, nothing synchronises on its own, and the
            link was a dead end. A new seller was told to wait for an event that would never
            happen. The only way into the Advisory MVP is an uploaded report, so that is what
            this now says. */}
        {showFirstRun && (
          <EmptyState
            className="mb-[18px]"
            title="Нет данных для анализа"
            description="PULT ставит диагноз по вашим отчётам с маркетплейса. Подойдёт выгрузка по продажам, товарам или возвратам в CSV или Excel — загрузите отчёт, и разбор появится здесь."
            action={
              <Link href="/dashboard/import">
                <Button variant="primary" size="sm">Загрузить отчёт →</Button>
              </Link>
            }
          />
        )}

        {/* still loading → draw neither: BusinessToday shows its own loading state, and the
            first-run screen must never flash at a seller who does have a diagnosis. */}
      </div>
    </>
  )
}
