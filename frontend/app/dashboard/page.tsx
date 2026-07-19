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
import { api, type FirstRunState } from '@/lib/api'

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

const _UPLOAD = (
  <Link href="/dashboard/import">
    <Button variant="primary" size="sm">Загрузить отчёт →</Button>
  </Link>
)

/** The first screen, told apart by WHY there is nothing to show yet. */
function FirstRunScreen({ state }: { state: FirstRunState | null }) {
  // The status call failed, so we do not know why there is nothing. Say the one thing that is
  // still true and offer the one road that exists — never guess at a state.
  if (!state || state.state === 'no_data') {
    return (
      <EmptyState
        className="mb-[18px]"
        title="Нет данных для анализа"
        description="PULT ставит диагноз по вашим отчётам с маркетплейса. Подойдёт выгрузка по продажам, товарам или возвратам в формате CSV — загрузите отчёт, и разбор появится здесь."
        action={_UPLOAD}
      />
    )
  }

  if (state.state === 'analyzing') {
    // Said only when a run is genuinely in flight, or an import is newer than the last finished
    // run. No estimate is given: nothing in the runtime promises one.
    return (
      <EmptyState
        className="mb-[18px]"
        title="Разбор готовится"
        description="Отчёт загружен, PULT его разбирает. Обновите страницу через несколько минут — результат появится здесь."
      />
    )
  }

  if (state.state === 'failed') {
    // Our breakage, not the seller's missing data. Saying "недостаточно данных" here would blame
    // them for it.
    return (
      <EmptyState
        className="mb-[18px]"
        title="Разбор не удался"
        description="Данные загружены, но проанализировать их не получилось. Мы уже видим эту ошибку — попробуйте обновить страницу позже."
      />
    )
  }

  // insufficient — the analysis finished and could conclude nothing. Name the gap.
  return (
    <EmptyState
      className="mb-[18px]"
      title="Данных пока недостаточно для разбора"
      description="Отчёт загружен и проанализирован, но для выводов не хватает данных."
      action={
        <div className="flex flex-col items-center gap-3">
          {state.missing.length > 0 && (
            <ul className="text-left text-[13px] leading-[1.6] max-w-[520px] list-disc pl-5"
                style={{ color: 'var(--text-2)' }}>
              {state.missing.map((m) => <li key={m}>{m}</li>)}
            </ul>
          )}
          {_UPLOAD}
        </div>
      }
    />
  )
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
  // Why the seller is where they are, from the server's evidence rather than our guess. Null
  // while loading; null forever if the call fails, in which case the screen falls back to the
  // old two-signal behaviour instead of inventing a state.
  const [firstRun, setFirstRun] = useState<FirstRunState | null>(null)

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

    api.today.getFirstRun()
      .then((r) => { if (alive) setFirstRun(r) })
      // Fail QUIET: without this the screen still works, it just cannot explain itself.
      .catch(() => { if (alive) setFirstRun(null) })

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

        {/* Nothing to show yet — but WHY there is nothing decides what the seller is told.
            This used to be a single "Нет данных для анализа" screen shown to everyone, including
            a seller who had just uploaded a report and been told PULT was analysing it. It sent
            them straight back to the import page they had come from. Each state below is backed
            by real evidence from the server; none of them promises a time, because the runtime
            guarantees none. */}
        {showFirstRun && <FirstRunScreen state={firstRun} />}

        {/* still loading → draw neither: BusinessToday shows its own loading state, and the
            first-run screen must never flash at a seller who does have a diagnosis. */}
      </div>
    </>
  )
}
