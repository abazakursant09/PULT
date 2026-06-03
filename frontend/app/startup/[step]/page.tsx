'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter, useParams } from 'next/navigation'
import {
  Target, ShoppingBag, Building2, Package, ImageIcon, TrendingUp,
  ChevronLeft, ChevronRight, CheckCircle2, Lock, Sparkles, RefreshCw,
  Download, Upload, Calculator, Shield, Truck, Loader2,
} from 'lucide-react'
import { Label } from '@/components/ui/label'

// ─── Palette
const BG  = '#F8F9FA'
const C   = 'rgba(22,22,28,0.9)'
const A   = '#3B82F6'
const T   = '#FFFFFF'
const M   = '#9CA3AF'
const ABG = 'rgba(26,115,232,0.08)'
const ABR = 'rgba(26,115,232,0.22)'

const TOTAL  = 6
const TITLES = ['', 'Выбор ниши и товара', 'Выбор площадки', 'Регистрация бизнеса', 'Первая поставка', 'Создание карточки товара', 'Первые продажи']
const ICONS  = [null, Target, ShoppingBag, Building2, Package, ImageIcon, TrendingUp] as const

// ─── Shared input style
const inp: React.CSSProperties = {
  width: '100%', background: 'rgba(13,13,15,0.6)', border: '1px solid rgba(26,115,232,0.18)',
  borderRadius: 12, padding: '12px 14px', fontSize: '0.9375rem', color: T,
  outline: 'none', fontFamily: 'inherit',
}

const RECS: Record<string, { niche: string; platform: string; margin: string; why: string }> = {
  'Одежда':          { niche: 'базовые трикотажные изделия (термобельё, носки)', platform: 'Wildberries', margin: '20–28%', why: 'наибольшая аудитория и спрос в категории «одежда»' },
  'Электроника':     { niche: 'аксессуары для смартфонов (держатели, кабели)',   platform: 'Ozon',        margin: '18–25%', why: 'ниже конкуренция, комиссия 4–15%' },
  'Handmade':        { niche: 'декоративные свечи ручной работы',               platform: 'Ozon',        margin: '35–50%', why: 'растущий спрос на уникальные изделия ручной работы' },
  'Товары для дома': { niche: 'домашний текстиль — подушки и пледы',            platform: 'Ozon',        margin: '25–30%', why: 'ниже конкуренция и комиссия 4–15%, лояльнее к новым продавцам' },
  'Другое':          { niche: 'органайзеры и товары для хранения',              platform: 'Яндекс Маркет', margin: '22–30%', why: 'низкая комиссия 3–9%' },
}

const PLATFORMS = [
  { name: 'Wildberries', fee: '5–25%', audience: '50+ млн', entry: '10 000 ₽', categories: 'Одежда, обувь, текстиль' },
  { name: 'Ozon',        fee: '4–15%', audience: '35+ млн', entry: '10 000 ₽', categories: 'Электроника, дом, красота' },
  { name: 'Яндекс Маркет', fee: '3–9%', audience: '20+ млн', entry: '15 000 ₽', categories: 'Электроника, бытовая техника' },
]

const SUPPLIERS = [
  { name: 'ООО ТекстильОпт',    min: '15 000 ₽', delivery: '5–7 дней',  rating: '4.8 ★' },
  { name: 'Фабрика Текстиль РФ', min: '20 000 ₽', delivery: '3–5 дней', rating: '4.6 ★' },
  { name: 'ГлавТекстиль',        min: '10 000 ₽', delivery: '7–10 дней', rating: '4.5 ★' },
  { name: 'МегаОпт.рф',          min: '25 000 ₽', delivery: '2–4 дня',  rating: '4.9 ★' },
]

// ─────────────────────────────────────────────────────────────────── STEP 1
function Step1({ onNext }: { onNext: () => void }) {
  const [form, setForm]     = useState({ category: 'Товары для дома', segment: 'Средний', experience: 'Нет', budget: '' })
  const [submitted, setSubmit] = useState(false)

  const rec = RECS[form.category] || RECS['Товары для дома']

  function submit() {
    if (!form.budget) return
    if (typeof window !== 'undefined') localStorage.setItem('startup_step1', JSON.stringify(form))
    setSubmit(true)
  }

  if (!submitted) return (
    <div style={{ background: C, borderRadius: 18, padding: 24, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: '1px solid rgba(26,115,232,0.12)' }}>
      <h3 style={{ fontSize: '1rem', fontWeight: 700, color: T, marginBottom: 4 }}>Расскажите о вашем товаре</h3>
      <p style={{ color: M, fontSize: '0.8125rem', marginBottom: 18 }}>Заполните форму — мы подберём оптимальную нишу с расчётом маржинальности.</p>
      <div className="space-y-4">
        <div>
          <Label className="mb-1.5">Категория товара</Label>
          <select style={{ ...inp, cursor: 'pointer', appearance: 'none', paddingRight: 36, backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='11' height='11' viewBox='0 0 24 24' fill='none' stroke='%235A5A5A' stroke-width='2'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E")`, backgroundRepeat: 'no-repeat', backgroundPosition: 'right 12px center' }}
            value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))}>
            {['Одежда', 'Электроника', 'Handmade', 'Товары для дома', 'Другое'].map(c => <option key={c}>{c}</option>)}
          </select>
        </div>
        <div>
          <Label className="mb-1.5">Ценовой сегмент</Label>
          <select style={{ ...inp, cursor: 'pointer', appearance: 'none', paddingRight: 36, backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='11' height='11' viewBox='0 0 24 24' fill='none' stroke='%235A5A5A' stroke-width='2'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E")`, backgroundRepeat: 'no-repeat', backgroundPosition: 'right 12px center' }}
            value={form.segment} onChange={e => setForm(f => ({ ...f, segment: e.target.value }))}>
            {['Эконом', 'Средний', 'Премиум'].map(s => <option key={s}>{s}</option>)}
          </select>
        </div>
        <div>
          <Label className="mb-1.5">Стартовый бюджет, ₽</Label>
          <input style={inp} type="number" placeholder="30 000" min="0"
            value={form.budget} onChange={e => setForm(f => ({ ...f, budget: e.target.value }))} />
        </div>
        <div>
          <Label className="mb-2">Есть опыт в продажах?</Label>
          <div className="flex gap-3">
            {['Да', 'Нет'].map(v => (
              <button key={v} type="button" onClick={() => setForm(f => ({ ...f, experience: v }))}
                className="flex-1 py-2.5 rounded-xl text-sm font-medium transition-all duration-200"
                style={form.experience === v
                  ? { background: ABG, color: A, border: `1px solid ${ABR}` }
                  : { background: 'transparent', color: M, border: '1px solid rgba(26,115,232,0.15)' }}
              >{v}</button>
            ))}
          </div>
        </div>
        <button onClick={submit} disabled={!form.budget}
          style={{ width: '100%', paddingTop: 13, paddingBottom: 13, borderRadius: 12, fontWeight: 600, border: 'none', cursor: form.budget ? 'pointer' : 'not-allowed', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, fontSize: '0.9375rem', ...(form.budget ? { background: A, color: '#fff', boxShadow: '0 4px 14px rgba(26,115,232,0.25)' } : { background: 'rgba(0,0,0,0.04)', color: 'rgba(0,0,0,0.38)' }) }}>
          Получить рекомендацию
        </button>
      </div>
    </div>
  )

  return (
    <div className="space-y-4">
      <div style={{ background: C, borderRadius: 18, padding: 22, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: `1px solid ${ABR}` }}>
        <div className="flex items-center gap-2 mb-3">
          <Sparkles size={14} style={{ color: A }} />
          <span style={{ fontSize: '0.7rem', fontWeight: 600, color: A, textTransform: 'uppercase', letterSpacing: '0.12em' }}>Ваша ниша</span>
        </div>
        <p style={{ fontSize: '1rem', color: T, lineHeight: 1.7, marginBottom: 12 }}>
          Для вас оптимально: <strong>{rec.niche}</strong>, {form.segment.toLowerCase()} ценовой сегмент, старт на <strong style={{ color: A }}>{rec.platform}</strong>.
        </p>
        <div className="flex flex-wrap gap-2">
          <span style={{ padding: '4px 10px', borderRadius: 20, fontSize: '0.75rem', fontWeight: 600, background: ABG, color: A, border: `1px solid ${ABR}` }}>Маржинальность: {rec.margin}</span>
          <span style={{ padding: '4px 10px', borderRadius: 20, fontSize: '0.75rem', fontWeight: 600, background: 'rgba(0,0,0,0.04)', color: M, border: '1px solid rgba(0,0,0,0.06)' }}>Бюджет: {parseInt(form.budget).toLocaleString('ru-RU')} ₽</span>
          <span style={{ padding: '4px 10px', borderRadius: 20, fontSize: '0.75rem', fontWeight: 600, background: 'rgba(0,0,0,0.04)', color: M, border: '1px solid rgba(0,0,0,0.06)' }}>{rec.platform}</span>
        </div>
      </div>
      <button onClick={onNext} style={{ width: '100%', paddingTop: 14, paddingBottom: 14, borderRadius: 12, background: A, color: '#fff', fontWeight: 600, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, fontSize: '0.9375rem', boxShadow: '0 4px 14px rgba(26,115,232,0.25)' }}>
        Перейти к выбору площадки <ChevronRight size={16} />
      </button>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────── STEP 2
function Step2({ onNext }: { onNext: () => void }) {
  const step1 = (() => { if (typeof window === 'undefined') return { category: 'Товары для дома' }; const d = localStorage.getItem('startup_step1'); return d ? JSON.parse(d) : { category: 'Товары для дома' } })()
  const [selected, setSelected] = useState('')
  const rec = RECS[step1.category] || RECS['Товары для дома']

  function choose(p: string) {
    setSelected(p)
    if (typeof window !== 'undefined') localStorage.setItem('startup_step2', JSON.stringify({ platform: p }))
  }

  return (
    <div className="space-y-5">
      <div style={{ background: C, borderRadius: 18, padding: 22, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: `1px solid ${ABR}` }}>
        <div className="flex items-center gap-2 mb-2">
          <Sparkles size={13} style={{ color: A }} />
          <span style={{ fontSize: '0.7rem', fontWeight: 600, color: A, textTransform: 'uppercase', letterSpacing: '0.12em' }}>Рекомендация</span>
        </div>
        <p style={{ color: T, lineHeight: 1.7, fontSize: '0.9375rem' }}>
          Для <strong>{step1.category?.toLowerCase()}</strong> лучше начать с <strong style={{ color: A }}>{rec.platform}</strong> — {rec.why}.
        </p>
      </div>

      <div style={{ background: C, borderRadius: 18, padding: 22, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: '1px solid rgba(26,115,232,0.12)' }}>
        <h3 style={{ fontSize: '0.9375rem', fontWeight: 600, color: T, marginBottom: 14 }}>Сравнение площадок</h3>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid rgba(26,115,232,0.1)' }}>
                {['Площадка', 'Комиссия', 'Аудитория', 'Порог входа', 'Категории-лидеры'].map(h => (
                  <th key={h} className="text-xs font-semibold uppercase tracking-wide text-muted-foreground" style={{ textAlign: 'left', padding: '6px 8px 10px', whiteSpace: 'nowrap' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {PLATFORMS.map(p => (
                <tr key={p.name} style={{ borderBottom: '1px solid rgba(26,115,232,0.08)', background: selected === p.name ? ABG : 'transparent' }}>
                  <td style={{ padding: '9px 8px', fontWeight: 600, color: selected === p.name ? A : T }}>{p.name}</td>
                  <td style={{ padding: '9px 8px', color: M }}>{p.fee}</td>
                  <td style={{ padding: '9px 8px', color: M }}>{p.audience}</td>
                  <td style={{ padding: '9px 8px', color: M }}>{p.entry}</td>
                  <td style={{ padding: '9px 8px', color: M }}>{p.categories}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        {PLATFORMS.map(p => (
          <button key={p.name} onClick={() => choose(p.name)}
            style={{ flex: 1, minWidth: 100, padding: '10px 6px', borderRadius: 12, fontSize: '0.875rem', fontWeight: selected === p.name ? 600 : 400, cursor: 'pointer', transition: 'all 0.2s', ...(selected === p.name ? { background: A, color: '#fff', border: `1px solid ${A}` } : { background: 'transparent', color: M, border: '1px solid rgba(26,115,232,0.15)' }) }}>
            {p.name}
          </button>
        ))}
      </div>

      {selected && (
        <button onClick={onNext} style={{ width: '100%', paddingTop: 14, paddingBottom: 14, borderRadius: 12, background: A, color: '#fff', fontWeight: 600, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, fontSize: '0.9375rem', boxShadow: '0 4px 14px rgba(26,115,232,0.25)' }}>
          Выбрать {selected} и перейти к регистрации <ChevronRight size={16} />
        </button>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────── STEP 3
function Step3({ onNext }: { onNext: () => void }) {
  type BizType = 'self' | 'ip' | null
  type Status  = 'initial' | 'ready' | 'sent' | 'registered'

  const [bizType, setBizType] = useState<BizType>(null)
  const [form, setForm]       = useState({ fio: '', inn: '', phone: '', passport: '', address: '', okved: '47.91' })
  const [status, setStatus]   = useState<Status>('initial')

  const statuses = [
    { key: 'initial',    label: 'Форма' },
    { key: 'ready',      label: 'Документы' },
    { key: 'sent',       label: 'Отправлено' },
    { key: 'registered', label: 'Готово' },
  ]
  const si = statuses.findIndex(s => s.key === status)

  const canPrepare = !!(form.fio && form.inn && form.phone)

  if (!bizType) return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      {([
        { key: 'self' as const, title: 'Самозанятость',  fee: '0 ₽',    desc: 'Налог 4–6%, без отчётности, до 2.4 млн ₽/год. Идеально для старта.' },
        { key: 'ip'   as const, title: 'ИП на УСН 6%',  fee: 'до 800 ₽', desc: 'Без ограничений по доходу. Подходит для масштабирования.' },
      ]).map(({ key, title, fee, desc }) => (
        <button key={key} onClick={() => setBizType(key)}
          style={{ background: C, borderRadius: 18, padding: 24, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: '1px solid rgba(26,115,232,0.12)', textAlign: 'left', cursor: 'pointer', transition: 'all 0.2s' }}
          onMouseEnter={e => { const el = e.currentTarget as HTMLElement; el.style.borderColor = ABR; el.style.boxShadow = `0 6px 20px rgba(26,115,232,0.12)` }}
          onMouseLeave={e => { const el = e.currentTarget as HTMLElement; el.style.borderColor = 'rgba(26,115,232,0.12)'; el.style.boxShadow = '0 2px 8px rgba(0,0,0,0.06)' }}
        >
          <span style={{ padding: '3px 10px', borderRadius: 20, fontSize: '0.75rem', fontWeight: 600, background: ABG, color: A, border: `1px solid ${ABR}`, display: 'inline-block', marginBottom: 10 }}>Регистрация: {fee}</span>
          <h3 style={{ fontSize: '1.125rem', fontWeight: 700, color: T, marginBottom: 6 }}>{title}</h3>
          <p style={{ fontSize: '0.875rem', color: M, lineHeight: 1.6 }}>{desc}</p>
        </button>
      ))}
    </div>
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 style={{ fontSize: '1rem', fontWeight: 700, color: T }}>{bizType === 'self' ? 'Самозанятость' : 'ИП на УСН 6%'}</h3>
        <button onClick={() => { setBizType(null); setStatus('initial') }} style={{ color: A, fontSize: '0.8125rem', background: 'none', border: 'none', cursor: 'pointer' }}>Изменить</button>
      </div>

      {/* Status bar */}
      <div style={{ background: C, borderRadius: 16, padding: '16px 20px', boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: '1px solid rgba(26,115,232,0.12)' }}>
        <div style={{ display: 'flex', alignItems: 'center' }}>
          {statuses.map((s, i) => (
            <div key={s.key} style={{ display: 'flex', alignItems: 'center', flex: 1 }}>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flex: 1 }}>
                <div style={{ width: 26, height: 26, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: '0.75rem', background: i <= si ? A : 'rgba(0,0,0,0.06)', color: i <= si ? '#F8F9FA' : M }}>
                  {i < si ? '✓' : i + 1}
                </div>
                <span style={{ fontSize: '0.6rem', marginTop: 4, color: i <= si ? A : M, letterSpacing: '0.04em', textTransform: 'uppercase', fontWeight: 600 }}>{s.label}</span>
              </div>
              {i < statuses.length - 1 && (
                <div style={{ height: 2, flex: 1, background: i < si ? A : 'rgba(0,0,0,0.06)', marginBottom: 18 }} />
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Form */}
      <div style={{ background: C, borderRadius: 18, padding: 22, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: '1px solid rgba(26,115,232,0.12)' }}>
        <div className="space-y-4">
          <div><Label className="mb-1.5">ФИО</Label><input style={inp} placeholder="Иванов Иван Иванович" value={form.fio} onChange={e => setForm(f => ({ ...f, fio: e.target.value }))} /></div>
          <div><Label className="mb-1.5">ИНН</Label><input style={inp} placeholder="123456789012" value={form.inn} onChange={e => setForm(f => ({ ...f, inn: e.target.value }))} /></div>
          <div><Label className="mb-1.5">Телефон</Label><input style={inp} placeholder="+7 900 000 00 00" value={form.phone} onChange={e => setForm(f => ({ ...f, phone: e.target.value }))} /></div>
          {bizType === 'ip' && <>
            <div><Label className="mb-1.5">Серия и номер паспорта</Label><input style={inp} placeholder="1234 567890" value={form.passport} onChange={e => setForm(f => ({ ...f, passport: e.target.value }))} /></div>
            <div><Label className="mb-1.5">Адрес регистрации</Label><input style={inp} placeholder="г. Москва, ул. Ленина, 1" value={form.address} onChange={e => setForm(f => ({ ...f, address: e.target.value }))} /></div>
            <div>
              <Label className="mb-1.5">ОКВЭД</Label>
              <input style={inp} value={form.okved} onChange={e => setForm(f => ({ ...f, okved: e.target.value }))} />
              <p style={{ fontSize: '0.75rem', color: M, marginTop: 4 }}>47.91 — торговля через интернет (подставлено автоматически)</p>
            </div>
          </>}
        </div>
        {status === 'initial' && (
          <button onClick={() => canPrepare && setStatus('ready')} disabled={!canPrepare}
            style={{ marginTop: 16, width: '100%', paddingTop: 12, paddingBottom: 12, borderRadius: 12, fontWeight: 600, border: 'none', cursor: canPrepare ? 'pointer' : 'not-allowed', ...(canPrepare ? { background: A, color: '#fff' } : { background: 'rgba(0,0,0,0.04)', color: 'rgba(0,0,0,0.38)' }) }}>
            Подготовить документы
          </button>
        )}
      </div>

      {status === 'ready' && (
        <div style={{ background: C, borderRadius: 16, padding: 20, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: `1px solid ${ABR}` }}>
          <div className="flex items-center gap-2 mb-2">
            <CheckCircle2 size={16} style={{ color: A }} />
            <span style={{ fontWeight: 600, color: T, fontSize: '0.9375rem' }}>Документы готовы</span>
          </div>
          <p style={{ color: M, fontSize: '0.8125rem', lineHeight: 1.65, marginBottom: 14 }}>
            {bizType === 'self' ? 'QR-код для «Мой налог» и заявление о переходе на самозанятость.' : 'Заполненное заявление Р21001 в PDF. Подайте его на Госуслугах или в МФЦ.'}
          </p>
          <div className="flex flex-col sm:flex-row gap-3">
            <button style={{ flex: 1, padding: '10px 14px', borderRadius: 10, background: ABG, color: A, border: `1px solid ${ABR}`, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, fontSize: '0.875rem', fontWeight: 600 }}>
              <Download size={13} /> Скачать {bizType === 'ip' ? 'Р21001' : 'заявление'}
            </button>
            <button onClick={() => setStatus('sent')} style={{ flex: 1, padding: '10px 14px', borderRadius: 10, background: 'rgba(0,0,0,0.04)', color: M, border: '1px solid rgba(0,0,0,0.06)', cursor: 'pointer', fontSize: '0.875rem' }}>
              Я отправил документы →
            </button>
          </div>
          <button onClick={onNext} style={{ marginTop: 10, width: '100%', paddingTop: 11, paddingBottom: 11, borderRadius: 12, background: A, color: '#fff', fontWeight: 600, border: 'none', cursor: 'pointer', fontSize: '0.875rem' }}>
            Пропустить шаг и продолжить →
          </button>
        </div>
      )}

      {status === 'sent' && (
        <div style={{ background: C, borderRadius: 16, padding: 20, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: `1px solid ${ABR}` }}>
          <div className="flex items-center gap-2 mb-2">
            <RefreshCw size={14} style={{ color: A }} />
            <span style={{ fontWeight: 600, color: T, fontSize: '0.9375rem' }}>Документы отправлены</span>
          </div>
          <p style={{ color: M, fontSize: '0.8125rem', marginBottom: 14 }}>Регистрация занимает 1–5 рабочих дней. Как получите подтверждение — нажмите ниже.</p>
          <button onClick={() => { setStatus('registered'); setTimeout(onNext, 600) }}
            style={{ width: '100%', paddingTop: 12, paddingBottom: 12, borderRadius: 12, background: A, color: '#fff', fontWeight: 600, border: 'none', cursor: 'pointer' }}>
            Я зарегистрирован! →
          </button>
        </div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────── STEP 4
function Step4({ onNext }: { onNext: () => void }) {
  const step1    = (() => { if (typeof window === 'undefined') return { category: 'Товары для дома' }; const d = localStorage.getItem('startup_step1'); return d ? JSON.parse(d) : { category: 'Товары для дома' } })()
  const [form, setForm]       = useState({ category: step1.category || '', budget: '' })
  const [submitted, setSubmit] = useState(false)

  if (!submitted) return (
    <div style={{ background: C, borderRadius: 18, padding: 24, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: '1px solid rgba(26,115,232,0.12)' }}>
      <h3 style={{ fontSize: '1rem', fontWeight: 700, color: T, marginBottom: 16 }}>Параметры первой поставки</h3>
      <div className="space-y-4">
        <div><Label className="mb-1.5">Категория товара</Label><input style={inp} value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))} /></div>
        <div><Label className="mb-1.5">Бюджет на закупку, ₽</Label><input style={inp} type="number" placeholder="20 000" value={form.budget} onChange={e => setForm(f => ({ ...f, budget: e.target.value }))} /></div>
        <button onClick={() => setSubmit(true)} disabled={!form.category || !form.budget}
          style={{ width: '100%', paddingTop: 12, paddingBottom: 12, borderRadius: 12, fontWeight: 600, border: 'none', cursor: (form.category && form.budget) ? 'pointer' : 'not-allowed', ...((form.category && form.budget) ? { background: A, color: '#fff' } : { background: 'rgba(0,0,0,0.04)', color: 'rgba(0,0,0,0.38)' }) }}>
          Найти поставщиков и рекомендации
        </button>
      </div>
    </div>
  )

  return (
    <div className="space-y-4">
      {/* Suppliers */}
      <div style={{ background: C, borderRadius: 18, padding: 22, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: '1px solid rgba(26,115,232,0.12)' }}>
        <h3 style={{ fontSize: '0.9375rem', fontWeight: 600, color: T, marginBottom: 12 }}>Проверенные поставщики</h3>
        <div className="space-y-2.5">
          {SUPPLIERS.map(s => (
            <div key={s.name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '11px 14px', borderRadius: 12, border: '1px solid rgba(26,115,232,0.1)', background: 'rgba(255,255,255,0.02)' }}>
              <div>
                <p style={{ fontWeight: 600, color: T, fontSize: '0.875rem' }}>{s.name}</p>
                <p style={{ color: M, fontSize: '0.75rem' }}>Мин. заказ: {s.min} · Доставка: {s.delivery}</p>
              </div>
              <span style={{ color: A, fontWeight: 600, fontSize: '0.875rem' }}>{s.rating}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Labeling */}
      <div style={{ background: C, borderRadius: 16, padding: 18, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: `1px solid ${ABR}` }}>
        <div className="flex items-center gap-2 mb-1.5">
          <Shield size={15} style={{ color: A }} />
          <span style={{ fontWeight: 600, color: T, fontSize: '0.9375rem' }}>Маркировка «Честный ЗНАК»</span>
        </div>
        <p style={{ color: M, fontSize: '0.8125rem' }}>Для вашей категории <strong style={{ color: A }}>маркировка не требуется</strong>. Уточните у поставщика перед заказом.</p>
      </div>

      {/* Packaging */}
      <div style={{ background: C, borderRadius: 16, padding: 18, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: '1px solid rgba(26,115,232,0.12)' }}>
        <h3 style={{ fontWeight: 600, color: T, fontSize: '0.9375rem', marginBottom: 10 }}>Рекомендации по упаковке</h3>
        <ul className="space-y-2">
          {['Полиэтиленовый пакет с логотипом — стандарт WB и Ozon', 'Бирка с артикулом и штрихкодом на каждой единице', 'Защитный наполнитель для хрупких товаров', 'Размер коробки по требованиям площадки'].map(r => (
            <li key={r} style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
              <CheckCircle2 size={13} style={{ color: A, marginTop: 2, flexShrink: 0 }} />
              <span style={{ fontSize: '0.8125rem', color: M }}>{r}</span>
            </li>
          ))}
        </ul>
      </div>

      {/* Delivery */}
      <div style={{ background: C, borderRadius: 16, padding: 18, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: '1px solid rgba(26,115,232,0.12)' }}>
        <div className="flex items-center gap-2 mb-3">
          <Truck size={15} style={{ color: A }} />
          <h3 style={{ fontWeight: 600, color: T, fontSize: '0.9375rem' }}>Сравнение служб доставки</h3>
        </div>
        <div className="space-y-2">
          {[
            { name: 'Логистика маркетплейса (FBO)', price: '40–80 ₽/ед.', time: '1–2 дня', best: true },
            { name: 'СДЭК',                         price: '120–250 ₽/отпр.', time: '3–5 дней', best: false },
            { name: 'Почта России',                  price: '80–150 ₽/отпр.', time: '5–14 дней', best: false },
          ].map(d => (
            <div key={d.name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '9px 12px', borderRadius: 10, background: d.best ? ABG : 'rgba(255,255,255,0.02)', border: d.best ? `1px solid ${ABR}` : '1px solid rgba(0,0,0,0.06)' }}>
              <div>
                <span style={{ fontWeight: d.best ? 600 : 400, color: d.best ? A : T, fontSize: '0.875rem' }}>{d.name}</span>
                {d.best && <span style={{ marginLeft: 6, fontSize: '0.625rem', fontWeight: 700, color: A, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Рекомендуем</span>}
              </div>
              <div style={{ textAlign: 'right' }}>
                <p style={{ color: d.best ? A : T, fontSize: '0.8125rem', fontWeight: 600 }}>{d.price}</p>
                <p style={{ color: M, fontSize: '0.75rem' }}>{d.time}</p>
              </div>
            </div>
          ))}
        </div>
      </div>

      <button onClick={onNext} style={{ width: '100%', paddingTop: 14, paddingBottom: 14, borderRadius: 12, background: A, color: '#fff', fontWeight: 600, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, boxShadow: '0 4px 14px rgba(26,115,232,0.25)' }}>
        Перейти к созданию карточки <ChevronRight size={16} />
      </button>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────── STEP 5
function Step5({ onNext }: { onNext: () => void }) {
  const [form, setForm]         = useState({ description: '', size: '', material: '', color: '' })
  const [hasPhoto, setHasPhoto] = useState(false)
  const [generating, setGen]    = useState(false)
  const [generated, setGenDone] = useState(false)
  const [card, setCard]         = useState({ title: '', description: '', chars: [] as string[] })
  const [showUpgrade, setUpgrade] = useState(false)

  function generate() {
    if (form.description.length < 10) return
    setGen(true)
    setTimeout(() => {
      setCard({
        title: `Плед флисовый уютный | ${form.material || 'Флис'} | ${form.size || '150×200 см'} | ${form.color || 'Бежевый'} | Подарок`,
        description: `${form.description} Подходит для ежедневного использования дома и в путешествиях. Материал — ${form.material || 'мягкий флис'}, размер ${form.size || '150×200 см'}, цвет ${form.color || 'бежевый'}. Быстрая доставка по России.`,
        chars: [`Материал: ${form.material || 'Флис'}`, `Размер: ${form.size || '150×200 см'}`, `Цвет: ${form.color || 'Бежевый'}`, 'Уход: машинная стирка 40°C', 'Страна производства: Россия'],
      })
      setGen(false)
      setGenDone(true)
    }, 2000)
  }

  if (!generated) return (
    <div style={{ background: C, borderRadius: 18, padding: 24, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: '1px solid rgba(26,115,232,0.12)' }}>
      <h3 style={{ fontSize: '1rem', fontWeight: 700, color: T, marginBottom: 16 }}>Данные для карточки</h3>

      {/* Photo upload */}
      <div style={{ marginBottom: 16 }}>
        <Label className="mb-1.5">Фото товара</Label>
        <div onClick={() => setHasPhoto(true)} style={{ border: `2px dashed ${hasPhoto ? A : 'rgba(26,115,232,0.2)'}`, borderRadius: 12, padding: '20px 16px', textAlign: 'center', cursor: 'pointer', background: hasPhoto ? ABG : 'transparent', transition: 'all 0.2s' }}>
          {hasPhoto
            ? <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, color: A, fontWeight: 600, fontSize: '0.9375rem' }}><CheckCircle2 size={17} /> Фото загружено</div>
            : <>
                <Upload size={20} style={{ color: M, margin: '0 auto 6px' }} />
                <p style={{ color: M, fontSize: '0.875rem' }}>Перетащите фото или нажмите для выбора</p>
                <p style={{ color: 'rgba(0,0,0,0.38)', fontSize: '0.75rem', marginTop: 3 }}>PNG, JPG до 10 МБ</p>
              </>}
        </div>
      </div>

      <div className="space-y-4">
        <div>
          <Label className="mb-1.5">Опишите товар своими словами</Label>
          <textarea style={{ ...inp, resize: 'none' }} rows={3} placeholder="Мягкий и уютный плед из флиса, идеально для холодных вечеров..."
            value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
        </div>
        <div className="grid grid-cols-3 gap-3">
          {([['size', 'Размер', '150×200 см'], ['material', 'Материал', 'Флис'], ['color', 'Цвет', 'Бежевый']] as const).map(([k, label, ph]) => (
            <div key={k}>
              <Label className="mb-1.5">{label}</Label>
              <input style={inp} placeholder={ph} value={form[k]} onChange={e => setForm(f => ({ ...f, [k]: e.target.value }))} />
            </div>
          ))}
        </div>
        <button onClick={generate} disabled={form.description.length < 10 || generating}
          style={{ width: '100%', paddingTop: 12, paddingBottom: 12, borderRadius: 12, fontWeight: 600, border: 'none', cursor: form.description.length >= 10 ? 'pointer' : 'not-allowed', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, ...((form.description.length >= 10 && !generating) ? { background: A, color: '#fff' } : { background: 'rgba(0,0,0,0.04)', color: 'rgba(0,0,0,0.38)' }) }}>
          {generating ? <><RefreshCw size={14} className="animate-spin" /> Генерируем...</> : <><Sparkles size={14} /> Сгенерировать карточку</>}
        </button>
      </div>
    </div>
  )

  return (
    <div className="space-y-4">
      <div style={{ background: C, borderRadius: 18, padding: 22, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: `1px solid ${ABR}` }}>
        <div className="flex items-center gap-2 mb-4">
          <Sparkles size={13} style={{ color: A }} />
          <span style={{ fontSize: '0.7rem', fontWeight: 600, color: A, textTransform: 'uppercase', letterSpacing: '0.12em' }}>Карточка готова</span>
        </div>
        <div className="space-y-4">
          <div><Label className="mb-1.5">SEO-заголовок</Label><input style={{ ...inp, fontSize: '0.875rem' }} value={card.title} onChange={e => setCard(c => ({ ...c, title: e.target.value }))} /></div>
          <div><Label className="mb-1.5">Описание</Label><textarea style={{ ...inp, resize: 'none', fontSize: '0.875rem' }} rows={4} value={card.description} onChange={e => setCard(c => ({ ...c, description: e.target.value }))} /></div>
          <div>
            <Label className="mb-2">Характеристики</Label>
            <div className="space-y-2">
              {card.chars.map((ch, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <CheckCircle2 size={12} style={{ color: A, flexShrink: 0 }} />
                  <input style={{ ...inp, padding: '8px 12px', fontSize: '0.8125rem' }} value={ch} onChange={e => setCard(c => ({ ...c, chars: c.chars.map((x, j) => j === i ? e.target.value : x) }))} />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {!showUpgrade ? (
        <div className="grid grid-cols-2 gap-3">
          {[{ label: '↓ Скачать карточку', Icon: Download }, { label: '↑ Опубликовать', Icon: Upload }].map(({ label, Icon }) => (
            <button key={label} onClick={() => setUpgrade(true)}
              style={{ padding: '12px', borderRadius: 12, background: 'rgba(0,0,0,0.04)', border: '1px solid rgba(0,0,0,0.06)', color: M, fontSize: '0.875rem', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, cursor: 'pointer' }}>
              <Lock size={12} /> {label}
            </button>
          ))}
        </div>
      ) : (
        <div style={{ background: C, borderRadius: 16, padding: 20, border: `2px solid ${ABR}`, boxShadow: '0 4px 20px rgba(26,115,232,0.1)' }}>
          <h3 style={{ fontWeight: 700, color: T, fontSize: '1rem', marginBottom: 6 }}>Активируйте «Мастер»</h3>
          <p style={{ color: M, fontSize: '0.8125rem', lineHeight: 1.65, marginBottom: 14 }}>
            Чтобы скачать или опубликовать карточку, активируйте «Мастер» — первый месяц уже включён в ваш тариф «Старт».
          </p>
          <button style={{ padding: '11px 20px', borderRadius: 12, background: A, color: '#fff', fontWeight: 600, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.875rem' }}>
            <Sparkles size={13} /> Активировать «Мастер»
          </button>
        </div>
      )}

      <button onClick={onNext} style={{ width: '100%', paddingTop: 14, paddingBottom: 14, borderRadius: 12, background: A, color: '#fff', fontWeight: 600, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, boxShadow: '0 4px 14px rgba(26,115,232,0.25)' }}>
        Перейти к первым продажам <ChevronRight size={16} />
      </button>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────── STEP 6
function Step6() {
  const router = useRouter()
  const [calc, setCalc] = useState({ price: '', cost: '', platform: 'Ozon' })
  const [result, setResult] = useState<{ profit: number; margin: number; monthly: number } | null>(null)

  const fees: Record<string, number> = { Wildberries: 0.15, Ozon: 0.1, 'Яндекс Маркет': 0.06 }

  function calculate() {
    const price = parseFloat(calc.price), cost = parseFloat(calc.cost)
    if (!price || !cost) return
    const fee    = price * (fees[calc.platform] || 0.1)
    const profit = price - cost - fee
    setResult({ profit: Math.round(profit), margin: Math.round((profit / price) * 100), monthly: Math.round(profit * 30) })
  }

  return (
    <div className="space-y-5">
      {/* Calculator */}
      <div style={{ background: C, borderRadius: 18, padding: 22, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: '1px solid rgba(26,115,232,0.12)' }}>
        <div className="flex items-center gap-2 mb-4">
          <Calculator size={16} style={{ color: A }} />
          <h3 style={{ fontWeight: 700, color: T, fontSize: '1rem' }}>Калькулятор прибыли</h3>
        </div>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div><Label className="mb-1.5">Цена товара, ₽</Label><input style={inp} type="number" placeholder="1 500" value={calc.price} onChange={e => setCalc(c => ({ ...c, price: e.target.value }))} /></div>
            <div><Label className="mb-1.5">Себестоимость, ₽</Label><input style={inp} type="number" placeholder="600" value={calc.cost} onChange={e => setCalc(c => ({ ...c, cost: e.target.value }))} /></div>
          </div>
          <div>
            <Label className="mb-1.5">Площадка</Label>
            <select style={{ ...inp, cursor: 'pointer', appearance: 'none', paddingRight: 36, backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='11' height='11' viewBox='0 0 24 24' fill='none' stroke='%235A5A5A' stroke-width='2'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E")`, backgroundRepeat: 'no-repeat', backgroundPosition: 'right 12px center' }}
              value={calc.platform} onChange={e => setCalc(c => ({ ...c, platform: e.target.value }))}>
              {['Wildberries', 'Ozon', 'Яндекс Маркет'].map(p => <option key={p}>{p}</option>)}
            </select>
          </div>
          <button onClick={calculate} disabled={!calc.price || !calc.cost}
            style={{ width: '100%', paddingTop: 12, paddingBottom: 12, borderRadius: 12, fontWeight: 600, border: 'none', cursor: (calc.price && calc.cost) ? 'pointer' : 'not-allowed', ...((calc.price && calc.cost) ? { background: A, color: '#fff' } : { background: 'rgba(0,0,0,0.04)', color: 'rgba(0,0,0,0.38)' }) }}>
            Рассчитать
          </button>
        </div>
        {result && (
          <div className="grid grid-cols-3 gap-3 mt-5">
            {[
              { label: 'Прибыль с 1 шт.',        value: `${result.profit > 0 ? '+' : ''}${result.profit.toLocaleString('ru-RU')} ₽`, ok: result.profit > 0 },
              { label: 'Маржинальность',           value: `${result.margin}%`,                                                        ok: result.margin > 15 },
              { label: 'Прогноз / мес.',           value: `${result.monthly.toLocaleString('ru-RU')} ₽`,                             ok: result.monthly > 0 },
            ].map(s => (
              <div key={s.label} style={{ background: s.ok ? ABG : 'rgba(255,255,255,0.03)', borderRadius: 12, padding: 12, border: s.ok ? `1px solid ${ABR}` : '1px solid rgba(0,0,0,0.06)' }}>
                <p style={{ fontSize: '1.0625rem', fontWeight: 700, color: s.ok ? A : T }}>{s.value}</p>
                <p style={{ fontSize: '0.6875rem', color: M, marginTop: 3 }}>{s.label}</p>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Advice */}
      <div style={{ background: C, borderRadius: 18, padding: 22, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: '1px solid rgba(26,115,232,0.12)' }}>
        <h3 style={{ fontWeight: 700, color: T, fontSize: '0.9375rem', marginBottom: 14 }}>Советы для быстрого старта</h3>
        <div className="space-y-3">
          {[
            { title: 'Запустите рекламу',              desc: 'Вложите 3 000–5 000 ₽ во внутреннюю рекламу площадки на первые 2 недели.' },
            { title: 'Соберите первые 10 отзывов',      desc: 'Программа «Отзывы за баллы» — первые отзывы критичны для ранжирования.' },
            { title: 'Цена на 5–10% ниже рынка',        desc: 'Поставьте цену чуть ниже конкурентов на первые 30 дней для органического роста.' },
          ].map(({ title, desc }) => (
            <div key={title} style={{ padding: '12px 14px', borderRadius: 12, background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(26,115,232,0.1)' }}>
              <p style={{ fontWeight: 600, color: T, fontSize: '0.875rem', marginBottom: 2 }}>{title}</p>
              <p style={{ color: M, fontSize: '0.8125rem', lineHeight: 1.6 }}>{desc}</p>
            </div>
          ))}
        </div>
      </div>

      {/* What's next */}
      <div style={{ background: C, borderRadius: 18, padding: 22, boxShadow: '0 2px 8px rgba(0,0,0,0.06)', border: `2px solid ${ABR}` }}>
        <div className="flex items-center gap-2 mb-3">
          <Sparkles size={15} style={{ color: A }} />
          <h3 style={{ fontWeight: 700, color: T, fontSize: '0.9375rem' }}>Что дальше?</h3>
        </div>
        <p style={{ color: M, lineHeight: 1.75, fontSize: '0.9375rem' }}>
          Вы запустили продажи. <strong style={{ color: T }}>Бизнес-Пульт берёт всё на автопилот</strong>: отзывы, цены, конкурентов, финансы. А если возникнут вопросы — ассистент поможет в любой момент.
        </p>
        <button onClick={() => router.push('/dashboard')}
          style={{ marginTop: 18, width: '100%', paddingTop: 15, paddingBottom: 15, borderRadius: 12, background: A, color: '#fff', fontWeight: 600, fontSize: '1rem', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, boxShadow: '0 4px 20px rgba(26,115,232,0.3)' }}>
          Перейти в Пульт <ChevronRight size={17} />
        </button>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────── MAIN
export default function StepPage() {
  const router   = useRouter()
  const params   = useParams()
  const stepNum  = parseInt(params.step as string, 10)
  const isValid  = stepNum >= 1 && stepNum <= TOTAL

  const [hasPlan, setHasPlan] = useState<boolean | null>(null)

  useEffect(() => {
    setHasPlan(typeof window !== 'undefined' && localStorage.getItem('startPlan') === 'active')
  }, [])

  const goNext = () => { if (stepNum < TOTAL) router.push(`/startup/${stepNum + 1}`) }
  const goPrev = () => { if (stepNum > 1) router.push(`/startup/${stepNum - 1}`); else router.push('/startup') }

  // Loading
  if (hasPlan === null) return (
    <div style={{ background: BG, minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <Loader2 size={28} className="animate-spin text-muted-foreground" />
    </div>
  )

  // Paywall
  if (!hasPlan || !isValid) return (
    <div style={{ background: BG, minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
      <div style={{ background: C, borderRadius: 20, padding: 48, maxWidth: 440, textAlign: 'center', boxShadow: '0 2px 8px rgba(0,0,0,0.06)' }}>
        <div style={{ width: 56, height: 56, borderRadius: '50%', background: ABG, border: `2px solid ${ABR}`, display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 18px' }}>
          <Lock size={24} style={{ color: A }} />
        </div>
        <h2 style={{ fontSize: '1.375rem', fontWeight: 700, color: T, marginBottom: 8 }}>
          {!isValid ? 'Шаг не найден' : 'Доступно с тарифом «Старт»'}
        </h2>
        <p style={{ color: M, marginBottom: 24, lineHeight: 1.65 }}>
          {!isValid ? 'Выберите шаг от 1 до 6.' : '6 шагов до первых продаж доступны после оформления тарифа «Старт» за 9 990 ₽.'}
        </p>
        <div className="flex flex-col gap-3">
          {!hasPlan && (
            <button onClick={() => router.push('/checkout?plan=start')}
              style={{ padding: '13px 24px', borderRadius: 12, background: A, color: '#fff', fontWeight: 600, border: 'none', cursor: 'pointer', boxShadow: '0 4px 16px rgba(26,115,232,0.3)' }}>
              Оформить «Старт» — 9 990 ₽
            </button>
          )}
          <Link href="/startup" style={{ color: A, fontSize: '0.875rem' }}>← Вернуться</Link>
        </div>
      </div>
    </div>
  )

  const StepIcon = ICONS[stepNum]!

  return (
    <div style={{ background: BG, minHeight: '100vh', color: T }}>

      {/* Nav */}
      <nav style={{ position: 'sticky', top: 0, zIndex: 50, background: 'rgba(13,13,15,0.92)', backdropFilter: 'blur(24px)', borderBottom: '1px solid rgba(26,115,232,0.1)' }}>
        <div className="max-w-2xl mx-auto px-6 h-14 flex items-center justify-between">
          <button onClick={goPrev} style={{ display: 'flex', alignItems: 'center', gap: 4, color: M, fontSize: '0.875rem', background: 'none', border: 'none', cursor: 'pointer' }}>
            <ChevronLeft size={15} /> {stepNum === 1 ? 'Точка старта' : 'Назад'}
          </button>
          <div style={{ display: 'flex', alignItems: 'baseline' }}>
            <span style={{ fontWeight: 700, color: T }}>Бизнес‑</span>
            <span style={{ fontWeight: 700, color: A }}>Пульт</span>
          </div>
          <span className="mono" style={{ fontSize: '0.8125rem', color: M }}>{stepNum} / {TOTAL}</span>
        </div>
      </nav>

      <div className="max-w-2xl mx-auto px-6 pt-8 pb-20">

        {/* Progress */}
        <div style={{ display: 'flex', gap: 6, marginBottom: 28 }}>
          {Array.from({ length: TOTAL }, (_, i) => i + 1).map(s => (
            <div key={s}
              onClick={() => s < stepNum && router.push(`/startup/${s}`)}
              style={{ flex: 1, height: 4, borderRadius: 99, background: s <= stepNum ? A : 'rgba(0,0,0,0.10)', cursor: s < stepNum ? 'pointer' : 'default', transition: 'background 0.3s' }}
            />
          ))}
        </div>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16, marginBottom: 28 }}>
          <div style={{ width: 48, height: 48, borderRadius: 14, background: ABG, border: `1px solid ${ABR}`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            <StepIcon size={22} style={{ color: A }} />
          </div>
          <div>
            <p style={{ fontSize: '0.6875rem', fontWeight: 600, color: A, textTransform: 'uppercase', letterSpacing: '0.12em', marginBottom: 4 }}>Шаг {stepNum} из {TOTAL}</p>
            <h1 style={{ fontSize: 'clamp(1.25rem, 3vw, 1.625rem)', fontWeight: 700, color: T, lineHeight: 1.25 }}>
              {TITLES[stepNum]}
            </h1>
          </div>
        </div>

        {/* Content */}
        {stepNum === 1 && <Step1 onNext={goNext} />}
        {stepNum === 2 && <Step2 onNext={goNext} />}
        {stepNum === 3 && <Step3 onNext={goNext} />}
        {stepNum === 4 && <Step4 onNext={goNext} />}
        {stepNum === 5 && <Step5 onNext={goNext} />}
        {stepNum === 6 && <Step6 />}

        {/* Footer prev for step 1 */}
        {stepNum === 1 && (
          <div style={{ marginTop: 20 }}>
            <button onClick={goPrev} style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '9px 14px', borderRadius: 10, background: 'rgba(0,0,0,0.04)', border: '1px solid rgba(0,0,0,0.06)', color: M, fontSize: '0.8125rem', cursor: 'pointer' }}>
              <ChevronLeft size={13} /> К точке старта
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
