'use client'
import { useEffect, useState } from 'react'

const KEY = 'pult_onboarded'

const STEPS = [
  { n: '1', t: 'Всё крутится вокруг товара', d: 'Открываете товар — и видите про него ВСЁ сразу: деньги, рекламу, отзывы, цены, риски. Не нужно прыгать по разделам.' },
  { n: '2', t: 'Светофор показывает проблему', d: '🔴 теряете деньги · 🟡 есть риск · 🟢 всё хорошо. Сразу видно, на что смотреть.' },
  { n: '3', t: 'Кнопка «Исправить» — Пульт сделает сам', d: 'Сначала «Проверить» (безопасно, ничего не меняет), потом «Выполнить» — и ставка/цена/ответ применятся в кабинете.' },
]

/**
 * OnboardingModal — первый вход. Объясняет новичку, как устроен ПУЛЬТ:
 * товар = центр · светофор · Проверить→Выполнить. Показывается один раз.
 */
export function OnboardingModal() {
  const [open, setOpen] = useState(false)
  useEffect(() => { try { if (!localStorage.getItem(KEY)) setOpen(true) } catch { /* ignore */ } }, [])

  function close() {
    setOpen(false)
    try { localStorage.setItem(KEY, '1') } catch { /* ignore */ }
  }
  if (!open) return null

  return (
    <div onClick={close} style={{
      position: 'fixed', inset: 0, background: 'rgba(10,12,16,.55)', backdropFilter: 'blur(3px)',
      display: 'grid', placeItems: 'center', zIndex: 100, padding: 20,
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 20,
        maxWidth: 560, width: '100%', padding: 28, boxShadow: '0 24px 70px rgba(0,0,0,.35)',
      }}>
        <h2 style={{ fontSize: 21, fontWeight: 800, color: 'var(--text)', marginBottom: 4 }}>Добро пожаловать в ПУЛЬТ 👋</h2>
        <div style={{ color: 'var(--text-3)', fontSize: 14, marginBottom: 20 }}>
          Это рабочее место селлера. Не отчёты — а понятные действия. Вот как тут всё устроено:
        </div>
        {STEPS.map(s => (
          <div key={s.n} style={{ display: 'flex', gap: 14, padding: 14, borderRadius: 13, background: 'var(--surface-h)', marginBottom: 10 }}>
            <span style={{ width: 30, height: 30, borderRadius: 9, background: 'var(--violet)', color: '#fff', display: 'grid', placeItems: 'center', fontWeight: 800, flex: '0 0 auto' }}>{s.n}</span>
            <div>
              <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)' }}>{s.t}</div>
              <div style={{ fontSize: 12.5, color: 'var(--text-2)', marginTop: 2 }}>{s.d}</div>
            </div>
          </div>
        ))}
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 18 }}>
          <button onClick={close} style={{ fontSize: 14, fontWeight: 700, padding: '11px 18px', borderRadius: 11, border: 0, cursor: 'pointer', background: 'var(--violet)', color: '#fff' }}>
            Понятно, начать →
          </button>
          <button onClick={close} style={{ background: 'none', border: 0, color: 'var(--text-3)', fontSize: 13, cursor: 'pointer' }}>Пропустить</button>
        </div>
      </div>
    </div>
  )
}
