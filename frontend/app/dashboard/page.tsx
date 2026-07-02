'use client'
import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { SellerBar } from '@/components/seller/Shell'
import DecisionFeedPanel from '@/components/decision-feed/DecisionFeedPanel'
import TodayFocus from '@/components/decision-feed/TodayFocus'
import BusinessToday from '@/components/dashboard/BusinessToday'

export default function Home() {
  const router = useRouter()
  useEffect(() => { if (!localStorage.getItem('token')) router.push('/login') }, [router])

  return (
    <>
      <SellerBar title="Главная" sub="Среда, 4 июня · доброе утро" />
      <div className="s-canvas">
        <BusinessToday />
        <TodayFocus />
        <DecisionFeedPanel skipTopAction />
      </div>
    </>
  )
}
