'use client'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { SellerBar } from '@/components/seller/Shell'
import DecisionFeedPanel from '@/components/decision-feed/DecisionFeedPanel'
import TodayFocus from '@/components/decision-feed/TodayFocus'
import BusinessToday from '@/components/dashboard/BusinessToday'
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
  // First-run gate: null = unknown (loading), true = has data, false = no data yet.
  // Read from the EXISTING /api/today/summary has_data flag. On error, fail open to the
  // normal dashboard so an active user is never trapped behind the first-run screen.
  const [hasData, setHasData] = useState<boolean | null>(null)

  useEffect(() => { if (!localStorage.getItem('token')) router.push('/login') }, [router])
  useEffect(() => { setSub(_buildSub(new Date())) }, [])
  useEffect(() => {
    let alive = true
    api.today.getSummary()
      .then((r) => { if (alive) setHasData(r.has_data) })
      .catch(() => { if (alive) setHasData(true) })
    return () => { alive = false }
  }, [])

  return (
    <>
      <SellerBar title="Главная" sub={sub} />
      <div className="s-canvas">
        <BusinessToday />

        {/* has data → normal dashboard */}
        {hasData === true && (
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
        {hasData === false && (
          <div className="s-card" style={{ marginBottom: 18 }}>
            <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)', margin: 0 }}>
              Нет данных для анализа
            </h2>
            <div style={{ fontSize: 13, color: 'var(--text-2)', marginTop: 10, lineHeight: 1.5 }}>
              PULT ставит диагноз по вашим отчётам с маркетплейса. Загрузите отчёт —
              и разбор появится здесь.
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text-3)', marginTop: 8 }}>
              Подойдёт выгрузка по продажам, товарам или возвратам в CSV или Excel.
            </div>
            <Link href="/dashboard/import" style={{
              display: 'inline-block', marginTop: 12, fontSize: 12.5, fontWeight: 700,
              color: 'var(--violet-text, var(--ac-2))',
            }}>
              Загрузить отчёт →
            </Link>
          </div>
        )}

        {/* hasData === null → loading; BusinessToday shows its own loading state */}
      </div>
    </>
  )
}
