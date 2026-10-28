'use client'
import { useState, useEffect, useRef } from 'react'
import { Cookie, X } from 'lucide-react'
import styles from './CookieBanner.module.css'

// LEGAL-PRELAUNCH-C2 — this banner is an ACKNOWLEDGEMENT notice, not a consent gate.
// The only cookie the product sets is the backend-managed, HttpOnly session cookie, which
// JavaScript cannot read or write. There is no analytics cookie and no behavioural tracking,
// so this component sets NO browser cookie at all — doing so would be a fictional claim. It
// only records, in localStorage, that the notice was shown on this device so it is not repeated.
type Ack = { acknowledged: boolean; timestamp: number }

export function CookieBanner() {
  const [visible, setVisible] = useState(false)
  const noticeRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!visible || !noticeRef.current) return
    const notice = noticeRef.current
    const body = document.body
    const previous = body.style.paddingBottom
    const basePadding = parseFloat(getComputedStyle(body).paddingBottom) || 0
    let applied = ''
    const reserveSpace = () => {
      applied = `${basePadding + Math.ceil(notice.getBoundingClientRect().height)}px`
      body.style.paddingBottom = applied
    }
    reserveSpace()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(reserveSpace)
    observer?.observe(notice)
    window.addEventListener('resize', reserveSpace)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', reserveSpace)
      if (body.style.paddingBottom === applied) body.style.paddingBottom = previous
    }
  }, [visible])

  useEffect(() => {
    const saved = localStorage.getItem('cookie_consent')
    if (!saved) setVisible(true)
  }, [])

  function acknowledge() {
    const ack: Ack = { acknowledged: true, timestamp: Date.now() }
    localStorage.setItem('cookie_consent', JSON.stringify(ack))
    setVisible(false)
  }

  if (!visible) return null

  return (
    <div ref={noticeRef} className={styles.notice} role="region" aria-label="Файлы cookie и локальное хранилище">
      <div className={styles.panel}>
        <Cookie className={styles.icon} size={22} aria-hidden="true" />
        <div className={styles.content}>
          <p className={styles.title}>Файлы cookie и локальное хранилище</p>
          <p className={styles.description}>
            Для защищённого входа используется необходимая сессионная cookie. Необязательная
            аналитика сейчас не используется. Настройки интерфейса хранятся локально в вашем браузере.
          </p>
        </div>
        <button type="button" onClick={acknowledge} className={styles.close} aria-label="Закрыть">
          <X size={18} aria-hidden="true" />
        </button>
        <div className={styles.actions}>
          <button type="button" className={styles.acknowledge} onClick={acknowledge}>Понятно</button>
          <a href="/privacy" className={styles.policy}>Политика конфиденциальности</a>
        </div>
      </div>
    </div>
  )
}
