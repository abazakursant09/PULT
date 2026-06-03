'use client'

import { useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { AppShell } from '@/components/AppShell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Save, Calculator, ArrowLeft } from 'lucide-react'

interface CalcForm {
  price:       string
  costPrice:   string
  commission:  string
  logistics:   string
  storage:     string
  advertising: string
  returns:     string
  tax:         string
}

interface SavedCalc {
  id:      string
  name:    string
  profit:  number
  margin:  number
  date:    string
}

const EMPTY: CalcForm = {
  price: '', costPrice: '', commission: '15', logistics: '200',
  storage: '5', advertising: '10', returns: '5', tax: '6',
}

function Field({ label, value, onChange, suffix = '', placeholder = '0' }: {
  label: string; value: string; onChange: (v: string) => void; suffix?: string; placeholder?: string
}) {
  return (
    <div>
      <label className="label mb-2">{label}{suffix && <span style={{ color: '#909096', marginLeft: 6, textTransform: 'none', letterSpacing: 0 }}>{suffix}</span>}</label>
      <Input
        type="number"
        placeholder={placeholder}
        value={value}
        onChange={e => onChange(e.target.value)}
        min="0"
      />
    </div>
  )
}

function Bar({ label, value, total, color = '#7C3AED' }: { label: string; value: number; total: number; color?: string }) {
  const pct = total > 0 ? Math.min((Math.abs(value) / total) * 100, 100) : 0
  return (
    <div>
      <div className="flex justify-between mb-1.5">
        <span className="text-[12px]" style={{ color: '#71717A' }}>{label}</span>
        <span className="text-[12px] mono" style={{ color: '#FFFFFF' }}>{value.toLocaleString('ru-RU')} ₽</span>
      </div>
      <div style={{ height: 4, background: 'rgba(255,255,255,0.06)', borderRadius: 0 }}>
        <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 0, transition: 'width 0.3s' }} />
      </div>
    </div>
  )
}

export default function ProfitCalculatorPage() {
  const router = useRouter()
  const [form,   setForm]   = useState<CalcForm>(EMPTY)
  const [saved,  setSaved]  = useState<SavedCalc[]>([])
  const [saving, setSaving] = useState(false)

  const set = (k: keyof CalcForm) => (v: string) => setForm(f => ({ ...f, [k]: v }))

  const calc = useCallback(() => {
    const price        = parseFloat(form.price)        || 0
    const costPrice    = parseFloat(form.costPrice)    || 0
    const commission   = price * (parseFloat(form.commission)  || 0) / 100
    const logistics    = parseFloat(form.logistics)    || 0
    const storage      = price * (parseFloat(form.storage)     || 0) / 100
    const advertising  = price * (parseFloat(form.advertising) || 0) / 100
    const returns      = price * (parseFloat(form.returns)     || 0) / 100
    const totalExp     = costPrice + commission + logistics + storage + advertising + returns
    const taxBase      = Math.max(price - totalExp, 0)
    const tax          = taxBase * (parseFloat(form.tax) || 0) / 100
    const profit       = price - totalExp - tax
    const margin       = price > 0 ? (profit / price) * 100 : 0
    return { price, costPrice, commission, logistics, storage, advertising, returns, tax, totalExp, profit, margin }
  }, [form])

  const r = calc()

  function handleSave() {
    if (!r.price) return
    setSaving(true)
    setTimeout(() => {
      setSaved(prev => [{
        id:     Date.now().toString(),
        name:   `Расчёт ${new Date().toLocaleDateString('ru-RU')}`,
        profit: Math.round(r.profit),
        margin: parseFloat(r.margin.toFixed(1)),
        date:   new Date().toLocaleDateString('ru-RU'),
      }, ...prev])
      setSaving(false)
    }, 400)
  }

  return (
    <AppShell>
      <div className="p-8">
        <button
          onClick={() => router.back()}
          className="flex items-center gap-1.5 text-[13px] mb-6"
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#71717A', padding: 0 }}
          onMouseEnter={e => { e.currentTarget.style.color = '#FFFFFF' }}
          onMouseLeave={e => { e.currentTarget.style.color = '#71717A' }}
        >
          <ArrowLeft size={14} /> Назад
        </button>
        <div className="mb-8">
          <p className="label mb-2">ИНСТРУМЕНТЫ</p>
          <h1 className="text-[22px] font-bold" style={{ color: '#FFFFFF' }}>Калькулятор чистой прибыли</h1>
          <p className="text-[13px] mt-1" style={{ color: '#71717A' }}>Рассчитайте реальную прибыль с учётом всех расходов маркетплейса</p>
        </div>

        <div className="grid grid-cols-5 gap-6 items-start">

          {/* LEFT: Form — 3 cols */}
          <div className="col-span-3 space-y-4">
            <div className="p-6 rounded-[8px]" style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.08)' }}>
              <p className="text-[13px] font-semibold mb-5" style={{ color: '#FFFFFF' }}>Данные товара</p>
              <div className="grid grid-cols-2 gap-4">
                <Field label="ЦЕНА ПРОДАЖИ" suffix="₽" value={form.price} onChange={set('price')} placeholder="1990" />
                <Field label="СЕБЕСТОИМОСТЬ" suffix="₽" value={form.costPrice} onChange={set('costPrice')} placeholder="800" />
                <Field label="КОМИССИЯ МП" suffix="%" value={form.commission} onChange={set('commission')} placeholder="15" />
                <Field label="ЛОГИСТИКА" suffix="₽/ед." value={form.logistics} onChange={set('logistics')} placeholder="200" />
                <Field label="ХРАНЕНИЕ" suffix="% от цены" value={form.storage} onChange={set('storage')} placeholder="5" />
                <Field label="РЕКЛАМА" suffix="% от цены" value={form.advertising} onChange={set('advertising')} placeholder="10" />
                <Field label="ВОЗВРАТЫ" suffix="% от цены" value={form.returns} onChange={set('returns')} placeholder="5" />
                <Field label="НАЛОГ (УСН)" suffix="%" value={form.tax} onChange={set('tax')} placeholder="6" />
              </div>
            </div>

            <div className="flex gap-3">
              <Button onClick={handleSave} loading={saving}>
                <Save size={14} /> Сохранить расчёт
              </Button>
              <Button variant="ghost" onClick={() => setForm(EMPTY)}>Сбросить</Button>
            </div>
          </div>

          {/* RIGHT: Result — 2 cols */}
          <div className="col-span-2 sticky top-20">
            <div className="p-6 rounded-[8px]" style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.08)' }}>
              <p className="label mb-4">РЕЗУЛЬТАТ</p>

              <div className="mb-6">
                <p className="text-[11px] mb-1" style={{ color: '#909096' }}>ЧИСТАЯ ПРИБЫЛЬ</p>
                <p
                  className="font-bold mono leading-none"
                  style={{ fontSize: 36, color: r.profit >= 0 ? '#FFFFFF' : '#F87171' }}
                >
                  {r.profit >= 0 ? '+' : ''}{Math.round(r.profit).toLocaleString('ru-RU')}
                  <span style={{ fontSize: 18, color: '#A78BFA', marginLeft: 6 }}>₽</span>
                </p>
              </div>

              <div className="mb-6 p-4 rounded-[8px]" style={{ background: '#09090B', border: '1px solid rgba(255,255,255,0.08)' }}>
                <div className="flex justify-between mb-1">
                  <span className="text-[12px]" style={{ color: '#71717A' }}>МАРЖА</span>
                  <span className="text-[14px] font-bold mono" style={{ color: r.margin >= 15 ? '#22C55E' : '#F87171' }}>
                    {r.margin.toFixed(1)}%
                  </span>
                </div>
                <div style={{ height: 4, background: 'rgba(255,255,255,0.06)' }}>
                  <div style={{ height: '100%', width: `${Math.max(0, Math.min(r.margin * 5, 100))}%`, background: r.margin >= 15 ? '#22C55E' : '#F87171', transition: 'width 0.3s' }} />
                </div>
              </div>

              <div className="space-y-3">
                {r.price > 0 && (
                  <>
                    <Bar label="Себестоимость" value={r.costPrice}   total={r.price} color="#909096" />
                    <Bar label="Комиссия МП"   value={r.commission}  total={r.price} color="#7C3AED" />
                    <Bar label="Логистика"     value={r.logistics}   total={r.price} color="#5A56D0" />
                    <Bar label="Хранение"      value={r.storage}     total={r.price} color="#4A47B0" />
                    <Bar label="Реклама"       value={r.advertising} total={r.price} color="#3C3A90" />
                    <Bar label="Возвраты"      value={r.returns}     total={r.price} color="#2E2C70" />
                    <Bar label="Налог"         value={r.tax}         total={r.price} color="#232150" />
                  </>
                )}
                {r.price === 0 && (
                  <p className="text-center py-4 text-[13px]" style={{ color: '#909096' }}>Введите цену продажи</p>
                )}
              </div>

              {r.price > 0 && (
                <div className="mt-5 pt-4" style={{ borderTop: '1px solid rgba(255,255,255,0.08)' }}>
                  <div className="flex justify-between text-[12px]">
                    <span style={{ color: '#71717A' }}>Итого расходов</span>
                    <span className="mono" style={{ color: '#FFFFFF' }}>{Math.round(r.totalExp + r.tax).toLocaleString('ru-RU')} ₽</span>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* History */}
        {saved.length > 0 && (
          <div className="mt-8">
            <p className="label mb-4">ИСТОРИЯ РАСЧЁТОВ</p>
            <div className="space-y-2">
              {saved.map(s => (
                <div
                  key={s.id}
                  className="flex items-center justify-between p-4 rounded-[8px] transition-colors duration-200"
                  style={{ background: '#111113', border: '1px solid rgba(255,255,255,0.08)' }}
                  onMouseEnter={e => { e.currentTarget.style.background = '#18181B' }}
                  onMouseLeave={e => { e.currentTarget.style.background = '#111113' }}
                >
                  <div className="flex items-center gap-4">
                    <Calculator size={14} style={{ color: '#909096' }} />
                    <div>
                      <p className="text-[13px] font-medium" style={{ color: '#FFFFFF' }}>{s.name}</p>
                      <p className="text-[11px]" style={{ color: '#909096' }}>{s.date}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-6">
                    <div className="text-right">
                      <p className="text-[11px] label mb-0.5">ПРИБЫЛЬ</p>
                      <p className="text-[14px] font-bold mono" style={{ color: s.profit >= 0 ? '#A78BFA' : '#F87171' }}>
                        {s.profit >= 0 ? '+' : ''}{s.profit.toLocaleString('ru-RU')} ₽
                      </p>
                    </div>
                    <div className="text-right">
                      <p className="text-[11px] label mb-0.5">МАРЖА</p>
                      <p className="text-[14px] font-bold mono" style={{ color: s.margin >= 15 ? '#22C55E' : '#F87171' }}>
                        {s.margin}%
                      </p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </AppShell>
  )
}
