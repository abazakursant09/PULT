'use client'

import { useEffect, useState } from 'react'
import { api, TransportCompanyEntry, DeliveryResult } from '@/lib/api'
import { Star, Truck, Phone, Clock, Scale, Package2, ChevronDown, ChevronUp } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectOption } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { BlurFade } from '@/components/ui/blur-fade'

function Stars({ rating }: { rating: number }) {
  return (
    <span className="flex items-center gap-0.5">
      {[1,2,3,4,5].map(n => (
        <Star key={n} size={12} style={{ color: n <= Math.round(rating) ? 'var(--violet)' : 'hsl(var(--border))', fill: n <= Math.round(rating) ? 'var(--violet)' : 'none' }} />
      ))}
      <span className="ml-1 text-xs text-muted-foreground">{rating.toFixed(1)}</span>
    </span>
  )
}

const DELIVERY_TYPE_LABELS: Record<string, string> = {
  auto: 'Авто', rail: 'ЖД', air: 'Авиа', express: 'Экспресс', cargo: 'Сборный груз',
}

function DeliveryBadges({ types }: { types: string | null }) {
  if (!types) return null
  return (
    <div className="flex flex-wrap gap-1">
      {types.split(',').map(t => (
        <Badge key={t} variant="outline" className="text-xs px-1.5 py-0 h-5">{DELIVERY_TYPE_LABELS[t.trim()] ?? t}</Badge>
      ))}
    </div>
  )
}

function CompanyCard({ tc }: { tc: TransportCompanyEntry }) {
  const [open, setOpen] = useState(false)
  return (
    <Card className={`shadow-stripe transition-all ${open ? 'border-primary/30' : 'border-border/60'}`}>
      <CardContent className="p-4">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0" style={{ background: 'rgba(110,106,252,0.10)', border: '1px solid rgba(110,106,252,0.15)' }}>
            <Truck size={16} style={{ color: 'var(--violet)' }} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-start justify-between gap-2">
              <div>
                <h3 className="font-semibold text-sm" style={{ color: '#FFFFFF' }}>{tc.name}</h3>
                {tc.region && <p className="text-xs text-muted-foreground mt-0.5">{tc.region}</p>}
              </div>
              <button onClick={() => setOpen(v => !v)} className="w-7 h-7 flex items-center justify-center rounded-lg border border-border text-muted-foreground hover:bg-muted transition-colors shrink-0">
                {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
              </button>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <Stars rating={tc.rating} />
              <span className="text-xs text-muted-foreground">{tc.total_reviews} отзывов</span>
              {tc.min_transit_days != null && (
                <span className="flex items-center gap-1 text-xs text-muted-foreground">
                  <Clock size={10} /> {tc.min_transit_days}–{tc.max_transit_days} дн.
                </span>
              )}
            </div>
            <div className="mt-2"><DeliveryBadges types={tc.delivery_types} /></div>
          </div>
        </div>
        {open && (
          <div className="mt-4 pt-4 border-t border-border space-y-3">
            {tc.description && <p className="text-sm text-muted-foreground">{tc.description}</p>}
            <div className="grid grid-cols-2 gap-2 text-sm text-muted-foreground">
              {tc.price_per_kg != null && <div className="flex items-center gap-1.5"><Scale size={13} />{tc.price_per_kg} ₽/кг</div>}
              {tc.price_per_m3 != null && <div className="flex items-center gap-1.5"><Package2 size={13} />{tc.price_per_m3} ₽/м³</div>}
              {tc.phone && <div className="flex items-center gap-1.5 col-span-2"><Phone size={13} />{tc.phone}</div>}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function CompareForm({ onResults }: { onResults: (r: DeliveryResult[]) => void }) {
  const [form, setForm]     = useState({ from_city: 'Москва', to_city: '', weight_kg: '', volume_m3: '', delivery_type: '' })
  const [loading, setLoading] = useState(false)
  const [err, setErr]       = useState('')

  async function compare() {
    setLoading(true); setErr('')
    try {
      const data = await api.logistics.compare({
        from_city: form.from_city, to_city: form.to_city,
        weight_kg: parseFloat(form.weight_kg), volume_m3: parseFloat(form.volume_m3),
        delivery_type: form.delivery_type || undefined,
      })
      onResults(data)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Ошибка')
    } finally { setLoading(false) }
  }

  const valid = form.from_city && form.to_city && form.weight_kg && form.volume_m3

  return (
    <Card className="shadow-stripe">
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2" style={{ color: '#FFFFFF' }}>
          <Truck size={16} style={{ color: 'var(--violet)' }} /> Сравнение стоимости доставки
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {[
            { label: 'Откуда',     key: 'from_city',  type: 'text',   placeholder: 'Москва' },
            { label: 'Куда',       key: 'to_city',    type: 'text',   placeholder: 'Санкт-Петербург' },
            { label: 'Вес (кг)',   key: 'weight_kg',  type: 'number', placeholder: '100' },
            { label: 'Объём (м³)', key: 'volume_m3',  type: 'number', placeholder: '0.5' },
          ].map(({ label, key, type, placeholder }) => (
            <div key={key} className="space-y-1">
              <Label className="text-xs">{label}</Label>
              <Input type={type} placeholder={placeholder} value={(form as Record<string, string>)[key]} onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))} />
            </div>
          ))}
          <div className="space-y-1">
            <Label className="text-xs">Тип доставки</Label>
            <Select value={form.delivery_type} onChange={e => setForm(f => ({ ...f, delivery_type: e.target.value }))}>
              <SelectOption value="">Любой</SelectOption>
              <SelectOption value="auto">Авто</SelectOption>
              <SelectOption value="rail">ЖД</SelectOption>
              <SelectOption value="air">Авиа</SelectOption>
              <SelectOption value="express">Экспресс</SelectOption>
              <SelectOption value="cargo">Сборный груз</SelectOption>
            </Select>
          </div>
          <div className="flex items-end">
            <Button className="w-full" onClick={compare} disabled={!valid || loading}>
              {loading ? 'Расчёт...' : 'Рассчитать'}
            </Button>
          </div>
        </div>
        {err && <p className="text-sm mt-2" style={{ color: '#dc2626' }}>{err}</p>}
      </CardContent>
    </Card>
  )
}

function CompareResults({ results }: { results: DeliveryResult[] }) {
  if (results.length === 0) return (
    <Card className="shadow-stripe">
      <CardContent className="p-8 text-center text-sm text-muted-foreground">Нет результатов по выбранным параметрам</CardContent>
    </Card>
  )

  return (
    <Card className="shadow-stripe overflow-hidden">
      <CardHeader className="pb-2 bg-muted/30">
        <CardTitle className="text-sm text-muted-foreground font-medium">Результаты (от дешевле к дороже)</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        {results.map((r, idx) => (
          <div key={r.company_id} className="flex items-center gap-4 px-5 py-4 border-b border-border/40 last:border-b-0" style={{ background: idx === 0 ? 'rgba(110,106,252,0.06)' : 'transparent' }}>
            <div className="w-7 h-7 rounded-full flex items-center justify-center text-sm font-bold shrink-0" style={{ background: idx === 0 ? 'rgba(110,106,252,0.12)' : 'hsl(var(--muted))', color: idx === 0 ? 'var(--violet)' : '#8898AA' }}>
              {idx + 1}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <p className="font-medium text-sm" style={{ color: '#FFFFFF' }}>{r.company_name}</p>
                {idx === 0 && <Badge variant="default" className="text-xs">Выгоднее</Badge>}
              </div>
              <div className="flex items-center gap-3 mt-1 flex-wrap">
                <Stars rating={r.rating} />
                {r.min_transit_days != null && <span className="text-xs text-muted-foreground">{r.min_transit_days}–{r.max_transit_days} дн.</span>}
                <DeliveryBadges types={r.delivery_types} />
              </div>
            </div>
            <div className="text-right shrink-0">
              <p className="text-lg font-bold" style={{ color: '#FFFFFF' }}>{r.estimated_cost.toLocaleString('ru-RU')} ₽</p>
              <p className="text-xs text-muted-foreground">оценочная стоимость</p>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

export default function LogisticsPage() {
  const [companies,       setCompanies]       = useState<TransportCompanyEntry[]>([])
  const [loading,         setLoading]         = useState(true)
  const [compareResults,  setCompareResults]  = useState<DeliveryResult[] | null>(null)

  useEffect(() => {
    api.logistics.listCompanies().then(setCompanies).catch(() => {}).finally(() => setLoading(false))
  }, [])

  return (
    <AppShell>
      <div className="flex-1 p-4 md:p-6" style={{ background: 'var(--bg)' }}>
        <div className="max-w-5xl mx-auto space-y-6">

          <BlurFade inView>
            <div>
              <h1 className="text-2xl font-bold" style={{ color: '#FFFFFF', letterSpacing: '-0.02em' }}>Транспортные компании</h1>
              <p className="text-sm text-muted-foreground mt-1">Каталог ТК и сервис сравнения стоимости доставки</p>
            </div>
          </BlurFade>

          <BlurFade inView delay={0.05}>
            <CompareForm onResults={r => setCompareResults(r)} />
          </BlurFade>

          {compareResults !== null && (
            <BlurFade inView>
              <CompareResults results={compareResults} />
            </BlurFade>
          )}

          <BlurFade inView delay={0.1}>
            <div>
              <h2 className="font-semibold mb-4" style={{ color: '#FFFFFF' }}>Все транспортные компании</h2>
              {loading ? (
                <div className="grid sm:grid-cols-2 gap-4">
                  {[1,2,3,4].map(i => <Skeleton key={i} className="h-28 rounded-xl" />)}
                </div>
              ) : (
                <div className="grid sm:grid-cols-2 gap-4">
                  {companies.map((tc, idx) => (
                    <BlurFade key={tc.id} delay={idx * 0.04} inView>
                      <CompanyCard tc={tc} />
                    </BlurFade>
                  ))}
                </div>
              )}
            </div>
          </BlurFade>
        </div>
      </div>
    </AppShell>
  )
}
