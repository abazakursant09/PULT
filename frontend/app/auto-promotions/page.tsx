'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { AppShell } from '@/components/AppShell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Plus, Trash2, Zap, TrendingDown, TrendingUp, Clock, ArrowLeft } from 'lucide-react'

interface Promotion {
  id:          string
  productName: string
  sku:         string
  marketplace: string
  currentPrice:number
  minPrice:    number
  status:      'active' | 'paused' | 'triggered'
  lastCheck:   string
}

const MOCK_PROMOS: Promotion[] = [
  {
    id: '1', productName: 'Крем для рук увлажняющий', sku: 'SKU-001',
    marketplace: 'Wildberries', currentPrice: 890, minPrice: 750,
    status: 'active', lastCheck: '2 мин назад',
  },
  {
    id: '2', productName: 'Шампунь с кератином 500 мл', sku: 'SKU-002',
    marketplace: 'Ozon', currentPrice: 650, minPrice: 550,
    status: 'triggered', lastCheck: '5 мин назад',
  },
  {
    id: '3', productName: 'Маска для лица антивозрастная', sku: 'SKU-003',
    marketplace: 'Wildberries', currentPrice: 1200, minPrice: 1000,
    status: 'paused', lastCheck: '1 ч назад',
  },
]

const STATUS_MAP = {
  active:    { label: 'Активна',   color: 'var(--success)' },
  paused:    { label: 'Пауза',     color: 'var(--text-3)' },
  triggered: { label: 'Сработала', color: 'var(--violet-text)' },
}

const EMPTY = { name: '', sku: '', marketplace: 'wildberries', currentPrice: '', minPrice: '' }

export default function AutoPromotionsPage() {
  const router = useRouter()
  const [promos,   setPromos]   = useState<Promotion[]>(MOCK_PROMOS)
  const [showForm, setShowForm] = useState(false)
  const [form,     setForm]     = useState(EMPTY)
  const [adding,   setAdding]   = useState(false)

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  function handleAdd(e: React.FormEvent) {
    e.preventDefault()
    if (!form.name || !form.currentPrice || !form.minPrice) return
    setAdding(true)
    setTimeout(() => {
      setPromos(prev => [{
        id:           Date.now().toString(),
        productName:  form.name,
        sku:          form.sku,
        marketplace:  form.marketplace === 'wildberries' ? 'Wildberries' : form.marketplace === 'ozon' ? 'Ozon' : 'Яндекс Маркет',
        currentPrice: parseFloat(form.currentPrice),
        minPrice:     parseFloat(form.minPrice),
        status:       'active',
        lastCheck:    'только что',
      }, ...prev])
      setForm(EMPTY)
      setShowForm(false)
      setAdding(false)
    }, 500)
  }

  function handleDelete(id: string) {
    setPromos(p => p.filter(x => x.id !== id))
  }

  function toggleStatus(id: string) {
    setPromos(p => p.map(x => x.id === id ? { ...x, status: x.status === 'active' ? 'paused' : 'active' } : x))
  }

  const activeCount   = promos.filter(p => p.status === 'active').length
  const triggeredCount = promos.filter(p => p.status === 'triggered').length

  return (
    <AppShell>
      <div className="p-8">
        {/* Back */}
        <button
          onClick={() => router.back()}
          className="flex items-center gap-1.5 text-[13px] mb-6"
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-3)', padding: 0 }}
          onMouseEnter={e => { e.currentTarget.style.color = '#FFFFFF' }}
          onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-3)' }}
        >
          <ArrowLeft size={14} /> Назад
        </button>
        {/* Header */}
        <div className="mb-8">
          <p className="label mb-2">ИНСТРУМЕНТЫ</p>
          <h1 className="text-[22px] font-bold mb-1" style={{ color: '#FFFFFF' }}>Автоакции</h1>
          <p className="text-[13px]" style={{ color: 'var(--text-3)' }}>
            Автоматическое управление ценами — снижение при падении конкурентов
          </p>
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-3 gap-4 mb-8">
          {[
            { label: 'ВСЕГО ПРАВИЛ', value: promos.length, icon: Zap },
            { label: 'АКТИВНЫХ',     value: activeCount,   icon: TrendingDown },
            { label: 'СРАБОТАЛО',    value: triggeredCount, icon: TrendingUp },
          ].map(({ label, value, icon: Icon }, i) => (
            <div key={i} className="p-5 rounded-[8px]" style={{ background: 'var(--surface)', border: '1px solid rgba(255,255,255,0.08)' }}>
              <div className="flex items-center justify-between mb-3">
                <p className="label">{label}</p>
                <Icon size={13} style={{ color: 'var(--text-2)' }} />
              </div>
              <p className="text-[28px] font-bold mono" style={{ color: '#FFFFFF' }}>{value}</p>
            </div>
          ))}
        </div>

        {/* Toolbar */}
        <div className="flex items-center justify-between mb-5">
          <p className="text-[14px] font-medium" style={{ color: '#FFFFFF' }}>Отслеживаемые товары</p>
          <Button size="sm" onClick={() => setShowForm(v => !v)} style={{ height: 36 }}>
            <Plus size={13} /> Добавить правило
          </Button>
        </div>

        {/* Add form */}
        {showForm && (
          <div className="mb-5 p-6 rounded-[8px]" style={{ background: 'var(--surface)', border: '1px solid rgba(255,255,255,0.08)' }}>
            <p className="text-[13px] font-semibold mb-5" style={{ color: '#FFFFFF' }}>Новое правило автоакции</p>
            <form onSubmit={handleAdd}>
              <div className="grid grid-cols-3 gap-4 mb-4">
                <div className="col-span-2">
                  <label className="label mb-2">НАЗВАНИЕ ТОВАРА</label>
                  <Input placeholder="Крем для рук..." value={form.name} onChange={set('name')} required />
                </div>
                <div>
                  <label className="label mb-2">МАРКЕТПЛЕЙС</label>
                  <select className="input" value={form.marketplace} onChange={set('marketplace')}>
                    <option value="wildberries">Wildberries</option>
                    <option value="ozon">Ozon</option>
                    <option value="yandex_market">Яндекс Маркет</option>
                  </select>
                </div>
                <div>
                  <label className="label mb-2">АРТИКУЛ (SKU)</label>
                  <Input placeholder="SKU-001" value={form.sku} onChange={set('sku')} />
                </div>
                <div>
                  <label className="label mb-2">ТЕКУЩАЯ ЦЕНА, ₽</label>
                  <Input type="number" placeholder="990" value={form.currentPrice} onChange={set('currentPrice')} required />
                </div>
                <div>
                  <label className="label mb-2">МИН. ЦЕНА, ₽</label>
                  <Input type="number" placeholder="750" value={form.minPrice} onChange={set('minPrice')} required />
                </div>
              </div>
              <div className="flex gap-3">
                <Button type="submit" loading={adding}>Добавить</Button>
                <Button type="button" variant="ghost" onClick={() => setShowForm(false)}>Отмена</Button>
              </div>
            </form>
          </div>
        )}

        {/* List */}
        {promos.length === 0 ? (
          <div className="py-20 text-center">
            <Zap size={32} className="mx-auto mb-4" style={{ color: '#3A3A40' }} />
            <p className="text-[15px] font-medium mb-2" style={{ color: 'var(--text-3)' }}>Правил пока нет</p>
            <p className="text-[13px] mb-6" style={{ color: 'var(--text-2)' }}>Добавьте первый товар для отслеживания</p>
            <Button onClick={() => setShowForm(true)}><Plus size={14} /> Добавить правило</Button>
          </div>
        ) : (
          <div className="space-y-2">
            {promos.map(p => {
              const st = STATUS_MAP[p.status]
              const discount = Math.round(((p.currentPrice - p.minPrice) / p.currentPrice) * 100)
              return (
                <div
                  key={p.id}
                  className="flex items-center gap-4 p-5 rounded-[8px] transition-colors duration-200"
                  style={{ background: 'var(--surface)', border: '1px solid rgba(255,255,255,0.08)' }}
                  onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-h)' }}
                  onMouseLeave={e => { e.currentTarget.style.background = 'var(--surface)' }}
                >
                  {/* Status dot */}
                  <div className="w-2 h-2 rounded-full shrink-0" style={{ background: st.color }} />

                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <p className="text-[14px] font-medium truncate" style={{ color: '#FFFFFF' }}>{p.productName}</p>
                      <span className="text-[11px]" style={{ color: 'var(--text-2)' }}>{p.marketplace}</span>
                    </div>
                    <div className="flex items-center gap-4 text-[12px]" style={{ color: 'var(--text-2)' }}>
                      {p.sku && <span>SKU: {p.sku}</span>}
                      <span className="flex items-center gap-1"><Clock size={10} /> {p.lastCheck}</span>
                    </div>
                  </div>

                  {/* Prices */}
                  <div className="flex items-center gap-6 shrink-0">
                    <div className="text-right">
                      <p className="text-[11px] label mb-0.5">ТЕКУЩАЯ</p>
                      <p className="text-[15px] font-bold mono" style={{ color: '#FFFFFF' }}>{p.currentPrice.toLocaleString('ru-RU')} ₽</p>
                    </div>
                    <div className="text-right">
                      <p className="text-[11px] label mb-0.5">МИН.</p>
                      <p className="text-[15px] font-bold mono" style={{ color: 'var(--violet-text)' }}>{p.minPrice.toLocaleString('ru-RU')} ₽</p>
                    </div>
                    <div className="text-right">
                      <p className="text-[11px] label mb-0.5">ДИАПАЗОН</p>
                      <p className="text-[15px] font-bold mono" style={{ color: 'var(--text-3)' }}>−{discount}%</p>
                    </div>
                  </div>

                  {/* Status label */}
                  <span className="text-[11px] font-semibold shrink-0" style={{ color: st.color }}>
                    {st.label}
                  </span>

                  {/* Actions */}
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={() => toggleStatus(p.id)}
                      className="text-[12px] transition-colors duration-200 px-3 py-1 rounded-[8px]"
                      style={{ background: 'transparent', color: 'var(--text-2)', border: '1px solid rgba(255,255,255,0.10)', cursor: 'pointer' }}
                      onMouseEnter={e => { e.currentTarget.style.color = '#FFFFFF'; e.currentTarget.style.borderColor = 'rgba(255,255,255,0.20)' }}
                      onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-2)'; e.currentTarget.style.borderColor = 'rgba(255,255,255,0.10)' }}
                    >
                      {p.status === 'active' ? 'Пауза' : 'Запустить'}
                    </button>
                    <button
                      onClick={() => handleDelete(p.id)}
                      style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-2)', padding: '4px 6px' }}
                      onMouseEnter={e => { e.currentTarget.style.color = 'var(--danger)' }}
                      onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-2)' }}
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </AppShell>
  )
}
