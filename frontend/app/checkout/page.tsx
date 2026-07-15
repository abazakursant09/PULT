'use client'

import Link from 'next/link'
import { ArrowRight } from 'lucide-react'

// L0.1 — the former /checkout was a FAKE payment: a setTimeout → localStorage stub that printed
// "Оплата прошла успешно!" without any charge, selling modules the Advisory MVP does not ship at
// prices that disagreed with every other surface. All of that is removed. There is no simulated
// payment, no localStorage plan-activation, and no claims about unshipped modules — only an honest
// statement that paid plans are not available yet.

export default function CheckoutPage() {
  return (
    <div style={{ background: 'var(--bg)', minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20, fontFamily: 'Inter, Arial, sans-serif' }}>
      <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 16, padding: 40, textAlign: 'center', maxWidth: 460 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: 'var(--text)', margin: '0 0 10px' }}>
          Платные тарифы пока недоступны
        </h1>
        <p style={{ fontSize: 14, color: 'var(--text-2)', lineHeight: 1.6, margin: '0 0 28px' }}>
          PULT сейчас работает в режиме Advisory MVP. Платные тарифы будут доступны позже.
        </p>
        <Link href="/dashboard" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, background: 'var(--violet)', color: '#FFFFFF', fontWeight: 700, fontSize: 14, padding: '13px 22px', borderRadius: 12, textDecoration: 'none' }}>
          В личный кабинет <ArrowRight size={16} />
        </Link>
      </div>
    </div>
  )
}
