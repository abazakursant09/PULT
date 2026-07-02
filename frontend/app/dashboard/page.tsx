'use client'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { SellerBar } from '@/components/seller/Shell'
import DecisionFeedPanel from '@/components/decision-feed/DecisionFeedPanel'
import TodayFocus from '@/components/decision-feed/TodayFocus'
import BusinessToday from '@/components/dashboard/BusinessToday'

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

  useEffect(() => { if (!localStorage.getItem('token')) router.push('/login') }, [router])
  useEffect(() => { setSub(_buildSub(new Date())) }, [])

  return (
    <>
      <SellerBar title="Главная" sub={sub} />
      <div className="s-canvas">
        <BusinessToday />
        <TodayFocus />
        <DecisionFeedPanel skipTopAction />
      </div>
    </>
  )
}
