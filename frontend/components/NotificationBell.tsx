'use client'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { Bell, Check, CheckCheck } from 'lucide-react'
import { api, type NotificationItem } from '@/lib/api'

const DOT_COLOR: Record<string, string> = {
  new_review:    'var(--violet)',
  offer_change:  'var(--warning)',
  trial_end:     'var(--danger)',
  limit_reached: 'var(--violet)',
}

function fmt(iso: string) {
  return new Date(iso).toLocaleString('ru-RU', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

interface Props {
  dropdownSide?: 'down' | 'right'
}

export function NotificationBell({ dropdownSide = 'down' }: Props) {
  const [open,          setOpen]          = useState(false)
  const [count,         setCount]         = useState(0)
  const [notifications, setNotifications] = useState<NotificationItem[]>([])
  const [loading,       setLoading]       = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // Poll unread count on mount + every 30 s
  useEffect(() => {
    fetchCount()
    const id = setInterval(fetchCount, 30_000)
    return () => clearInterval(id)
  }, [])

  // Load notifications when dropdown opens
  useEffect(() => {
    if (open) fetchNotifications()
  }, [open])

  // Close on outside click
  useEffect(() => {
    function handle(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [])

  async function fetchCount() {
    try {
      const d = await api.notifications.unreadCount()
      setCount(d.count)
    } catch {}
  }

  async function fetchNotifications() {
    setLoading(true)
    try {
      const d = await api.notifications.list(1, 5)
      setNotifications(d.items)
      setCount(d.unread_count)
    } catch {} finally {
      setLoading(false)
    }
  }

  async function markRead(id: string) {
    await api.notifications.markRead(id).catch(() => null)
    setNotifications(ns => ns.map(n => n.id === id ? { ...n, is_read: true } : n))
    setCount(c => Math.max(0, c - 1))
  }

  async function markAllRead() {
    await api.notifications.markAllRead().catch(() => null)
    setNotifications(ns => ns.map(n => ({ ...n, is_read: true })))
    setCount(0)
  }

  const dropdownStyle: React.CSSProperties =
    dropdownSide === 'right'
      ? { position: 'absolute', left: 'calc(100% + 10px)', top: 0, width: 300, zIndex: 200 }
      : { position: 'absolute', top: 'calc(100% + 8px)', right: 0, width: 300, zIndex: 200 }

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      {/* Bell button */}
      <button
        onClick={() => setOpen(v => !v)}
        title="Уведомления"
        style={{
          position: 'relative',
          width: 34, height: 34,
          borderRadius: 9,
          border: `1px solid ${open ? 'rgba(110,106,252,0.3)' : 'rgba(110,106,252,0.12)'}`,
          background: open ? 'rgba(110,106,252,0.08)' : 'transparent',
          color: open ? 'var(--violet)' : 'var(--text-2)',
          cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          flexShrink: 0,
        }}
      >
        <Bell size={15} />
        {count > 0 && (
          <span style={{
            position: 'absolute', top: -5, right: -5,
            minWidth: 16, height: 16, borderRadius: 8, padding: '0 4px',
            background: 'var(--danger)', color: '#fff',
            fontSize: 9, fontWeight: 700,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            lineHeight: 1, border: '1.5px solid var(--surface)',
          }}>
            {count > 9 ? '9+' : count}
          </span>
        )}
      </button>

      {/* Dropdown */}
      {open && (
        <div style={{
          ...dropdownStyle,
          background: 'var(--surface)',
          border: '1px solid rgba(255,255,255,0.08)',
          borderRadius: 14,
          boxShadow: '0 8px 32px rgba(0,0,0,0.13)',
          overflow: 'hidden',
        }}>
          {/* Header */}
          <div style={{
            padding: '12px 14px',
            borderBottom: '1px solid rgba(255,255,255,0.06)',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          }}>
            <span style={{ fontWeight: 600, fontSize: '0.875rem', color: 'var(--text)' }}>
              Уведомления {count > 0 && <span style={{ color: 'var(--danger)' }}>({count})</span>}
            </span>
            {count > 0 && (
              <button
                onClick={markAllRead}
                style={{ display: 'flex', alignItems: 'center', gap: 4, color: 'var(--violet-text)', fontSize: '0.75rem', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
              >
                <CheckCheck size={12} /> Прочитать все
              </button>
            )}
          </div>

          {/* List */}
          <div style={{ maxHeight: 320, overflowY: 'auto' }}>
            {loading && (
              <div style={{ padding: '18px', textAlign: 'center', color: 'var(--text-2)', fontSize: '0.8125rem' }}>
                Загрузка...
              </div>
            )}
            {!loading && notifications.length === 0 && (
              <div style={{ padding: '24px 14px', textAlign: 'center', color: 'var(--text-2)', fontSize: '0.8125rem' }}>
                Нет уведомлений
              </div>
            )}
            {!loading && notifications.map(n => (
              <div
                key={n.id}
                style={{
                  padding: '10px 14px',
                  borderBottom: '1px solid rgba(110,106,252,0.08)',
                  background: n.is_read ? 'transparent' : 'rgba(110,106,252,0.03)',
                  display: 'flex', gap: 9, alignItems: 'flex-start',
                }}
              >
                <div style={{
                  width: 7, height: 7, borderRadius: '50%', marginTop: 5, flexShrink: 0,
                  background: n.is_read ? 'rgba(110,106,252,0.15)' : (DOT_COLOR[n.type] ?? 'var(--violet)'),
                }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: '0.75rem', color: 'var(--text)', marginBottom: 2 }}>
                    {n.title}
                  </div>
                  <div style={{ fontSize: '0.6875rem', color: 'var(--text-3)', lineHeight: 1.5 }}>
                    {n.message}
                  </div>
                  <div style={{ fontSize: '0.625rem', color: 'var(--text-2)', marginTop: 3 }}>
                    {fmt(n.created_at)}
                  </div>
                </div>
                {!n.is_read && (
                  <button
                    onClick={() => markRead(n.id)}
                    title="Отметить прочитанным"
                    style={{ color: 'var(--text-2)', background: 'none', border: 'none', cursor: 'pointer', padding: 2, flexShrink: 0 }}
                  >
                    <Check size={11} />
                  </button>
                )}
              </div>
            ))}
          </div>

          {/* Footer */}
          <div style={{ padding: '9px 14px', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
            <Link
              href="/dashboard/notifications"
              onClick={() => setOpen(false)}
              style={{ display: 'block', textAlign: 'center', color: 'var(--violet-text)', fontSize: '0.8125rem', textDecoration: 'none' }}
            >
              Все уведомления →
            </Link>
          </div>
        </div>
      )}
    </div>
  )
}
