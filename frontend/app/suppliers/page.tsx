'use client'

import { useEffect, useState, useCallback } from 'react'
import { api, SupplierEntry, SupplierReviewEntry } from '@/lib/api'
import { Star, ShieldCheck, Globe, Phone, Package, Search, X, Building2, ChevronDown, ChevronUp } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { BlurFade } from '@/components/ui/blur-fade'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'

const INDUSTRIES = [
  { value: '', label: 'Все отрасли' },
  { value: 'clothing',    label: 'Одежда' },
  { value: 'textile',     label: 'Текстиль' },
  { value: 'electronics', label: 'Электроника' },
  { value: 'home_goods',  label: 'Товары для дома' },
  { value: 'cosmetics',   label: 'Косметика' },
  { value: 'furniture',   label: 'Мебель' },
  { value: 'food',        label: 'Продукты питания' },
  { value: 'metals',      label: 'Металлы' },
  { value: 'chemicals',   label: 'Химия' },
  { value: 'packaging',   label: 'Упаковка' },
  { value: 'machinery',   label: 'Оборудование' },
]

const PRODUCTS_BY_INDUSTRY: Record<string, { name: string; price: number; minQty: number; emoji: string }[]> = {
  clothing: [
    { name: 'Футболка оверсайз (OEM)', price: 320, minQty: 200, emoji: '👕' },
    { name: 'Спортивные штаны', price: 580, minQty: 100, emoji: '🩲' },
    { name: 'Куртка демисезонная', price: 1850, minQty: 50, emoji: '🧥' },
    { name: 'Худи с капюшоном', price: 760, minQty: 100, emoji: '👔' },
  ],
  textile: [
    { name: 'Хлопковая ткань 150г/м²', price: 180, minQty: 100, emoji: '🧵' },
    { name: 'Трикотажное полотно', price: 240, minQty: 50, emoji: '🧶' },
    { name: 'Джинсовая ткань 300г/м²', price: 320, minQty: 80, emoji: '👖' },
    { name: 'Флисовый материал', price: 190, minQty: 100, emoji: '🌀' },
  ],
  electronics: [
    { name: 'LED-дисплей 7" IPS', price: 1200, minQty: 50, emoji: '📺' },
    { name: 'Кабель USB-C 2м', price: 85, minQty: 200, emoji: '🔌' },
    { name: 'Беспроводные наушники', price: 1450, minQty: 100, emoji: '🎧' },
    { name: 'Умная колонка', price: 2100, minQty: 50, emoji: '📻' },
  ],
  home_goods: [
    { name: 'Постельный комплект Евро', price: 1200, minQty: 50, emoji: '🛏️' },
    { name: 'Кухонный набор 8пр.', price: 2800, minQty: 30, emoji: '🍳' },
    { name: 'Органайзер для ванной', price: 480, minQty: 100, emoji: '🧼' },
    { name: 'Настольная лампа LED', price: 890, minQty: 50, emoji: '💡' },
  ],
  furniture: [
    { name: 'Диван угловой', price: 18500, minQty: 5, emoji: '🛋️' },
    { name: 'Письменный стол', price: 6200, minQty: 10, emoji: '🖥️' },
    { name: 'Шкаф-купе 2-дверный', price: 14000, minQty: 5, emoji: '🚪' },
    { name: 'Кровать 160×200', price: 11000, minQty: 5, emoji: '🛏️' },
  ],
  cosmetics: [
    { name: 'Шампунь 300мл (OEM)', price: 95, minQty: 500, emoji: '🧴' },
    { name: 'Крем для рук 50мл', price: 65, minQty: 1000, emoji: '🫧' },
    { name: 'Маска для волос 200мл', price: 140, minQty: 300, emoji: '💆' },
    { name: 'Тональный крем SPF30', price: 210, minQty: 200, emoji: '✨' },
  ],
  food: [
    { name: 'Мука пшеничная (50кг)', price: 1850, minQty: 100, emoji: '🌾' },
    { name: 'Масло подсолнечное 5л', price: 480, minQty: 200, emoji: '🫙' },
    { name: 'Гречневая крупа 25кг', price: 1200, minQty: 50, emoji: '🌱' },
    { name: 'Сахар-песок 50кг', price: 2100, minQty: 80, emoji: '🍬' },
  ],
  machinery: [
    { name: 'Фрезерный станок ЧПУ', price: 285000, minQty: 1, emoji: '⚙️' },
    { name: 'Ленточный конвейер 3м', price: 42000, minQty: 1, emoji: '🏭' },
    { name: 'Гидравлический пресс 50т', price: 125000, minQty: 1, emoji: '🔧' },
    { name: 'Токарный станок ТВ-320', price: 68000, minQty: 1, emoji: '🪛' },
  ],
  packaging: [
    { name: 'Гофрокороб 40×30×30', price: 28, minQty: 500, emoji: '📦' },
    { name: 'Стрейч-пленка 17мкм', price: 320, minQty: 100, emoji: '🎁' },
    { name: 'Пакет ПВД 30×40', price: 8, minQty: 2000, emoji: '🛍️' },
    { name: 'Пакет zip-lock 15×20', price: 5, minQty: 3000, emoji: '🔒' },
  ],
  metals: [
    { name: 'Арматура А500С ø12мм', price: 68000, minQty: 5, emoji: '🔩' },
    { name: 'Профтруба 60×60×3мм', price: 4200, minQty: 100, emoji: '⬛' },
    { name: 'Лист стальной 2мм', price: 5800, minQty: 50, emoji: '🪨' },
    { name: 'Уголок равнополочный 50', price: 3200, minQty: 80, emoji: '📐' },
  ],
  chemicals: [
    { name: 'Растворитель 646 (20л)', price: 1200, minQty: 50, emoji: '🧪' },
    { name: 'Эпоксидная смола (5кг)', price: 1850, minQty: 20, emoji: '⚗️' },
    { name: 'Краска алкидная (10л)', price: 1450, minQty: 30, emoji: '🎨' },
    { name: 'Грунтовка ГФ-021 (5кг)', price: 980, minQty: 50, emoji: '🪣' },
  ],
}

const COUNTRIES = [
  { value: '',       label: 'Все страны' },
  { value: 'russia', label: 'Россия' },
  { value: 'china',  label: 'Китай' },
]

function Stars({ rating }: { rating: number }) {
  return (
    <span className="flex items-center gap-0.5">
      {[1,2,3,4,5].map(n => (
        <Star key={n} size={13} style={{ color: n <= Math.round(rating) ? 'var(--violet)' : 'hsl(var(--border))', fill: n <= Math.round(rating) ? 'var(--violet)' : 'none' }} />
      ))}
      <span className="ml-1 text-xs text-muted-foreground">{rating.toFixed(1)}</span>
    </span>
  )
}

function ReviewForm({ supplierId, onDone }: { supplierId: string; onDone: () => void }) {
  const [rating, setRating] = useState(5)
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  async function submit() {
    setLoading(true); setErr('')
    try {
      await api.supplierReviews.create({ target_type: 'supplier', target_id: supplierId, rating, text })
      onDone()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Ошибка')
    } finally { setLoading(false) }
  }

  return (
    <div className="rounded-xl p-4 mt-3 space-y-3 bg-muted/40 border border-border/60">
      <p className="font-medium text-sm" style={{ color: '#FFFFFF' }}>Оставить отзыв</p>
      <div className="flex gap-1">
        {[1,2,3,4,5].map(n => (
          <button key={n} onClick={() => setRating(n)}>
            <Star size={20} style={{ color: n <= rating ? 'var(--violet)' : 'hsl(var(--border))', fill: n <= rating ? 'var(--violet)' : 'none' }} />
          </button>
        ))}
      </div>
      <textarea
        className="w-full rounded-lg border border-border px-3 py-2 text-sm resize-none h-20 bg-background outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/40 transition-colors"
        placeholder="Комментарий (необязательно)"
        value={text}
        onChange={e => setText(e.target.value)}
      />
      {err && <p className="text-xs text-red-600">{err}</p>}
      <div className="flex gap-2">
        <Button size="sm" onClick={submit} disabled={loading}>{loading ? 'Отправка...' : 'Отправить'}</Button>
        <Button size="sm" variant="ghost" onClick={onDone}>Отмена</Button>
      </div>
    </div>
  )
}

function DealForm({ supplierId, onClose }: { supplierId: string; onClose: () => void }) {
  const [form, setForm] = useState({
    product_name: '', specification: '',
    price_per_unit: '', quantity: '', deadline: '',
  })
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState(false)

  async function submit() {
    setLoading(true); setErr('')
    try {
      await api.deals.create({
        supplier_id: supplierId,
        product_name: form.product_name,
        specification: form.specification || undefined,
        price_per_unit: parseFloat(form.price_per_unit),
        quantity: parseInt(form.quantity),
        deadline: form.deadline || undefined,
      })
      setDone(true)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Ошибка')
    } finally { setLoading(false) }
  }

  if (done) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}>
        <Card className="shadow-stripe-lg max-w-sm w-full text-center">
          <CardContent className="p-8">
            <div className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-4" style={{ background: 'rgba(110,106,252,0.1)', border: '1px solid rgba(110,106,252,0.25)' }}>
              <ShieldCheck size={28} style={{ color: 'var(--violet)' }} />
            </div>
            <h2 className="text-xl font-bold mb-2" style={{ color: '#FFFFFF' }}>Сделка создана!</h2>
            <p className="text-sm mb-6 text-muted-foreground">Договор сформирован. Перейдите в раздел «Сделки» для подписания.</p>
            <Button className="w-full" onClick={onClose}>Закрыть</Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}>
      <Card className="shadow-stripe-lg w-full max-w-lg">
        <CardHeader className="pb-3 border-b border-border/60">
          <div className="flex items-center justify-between">
            <CardTitle className="text-lg" style={{ color: '#FFFFFF' }}>Новая сделка</CardTitle>
            <button onClick={onClose} className="w-8 h-8 flex items-center justify-center rounded-lg border border-border text-muted-foreground hover:bg-muted transition-colors">
              <X size={16} />
            </button>
          </div>
        </CardHeader>
        <CardContent className="p-5 space-y-3">
          <div className="space-y-1">
            <Label className="text-xs">Наименование товара *</Label>
            <Input value={form.product_name} onChange={e => setForm(f => ({ ...f, product_name: e.target.value }))} />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Спецификация</Label>
            <textarea
              className="w-full rounded-lg border border-border px-3 py-2 text-sm resize-none h-16 bg-background outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/40 transition-colors"
              value={form.specification}
              onChange={e => setForm(f => ({ ...f, specification: e.target.value }))}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label className="text-xs">Цена за ед. (руб.) *</Label>
              <Input type="number" value={form.price_per_unit} onChange={e => setForm(f => ({ ...f, price_per_unit: e.target.value }))} />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Количество *</Label>
              <Input type="number" value={form.quantity} onChange={e => setForm(f => ({ ...f, quantity: e.target.value }))} />
            </div>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Срок поставки</Label>
            <Input type="date" value={form.deadline} onChange={e => setForm(f => ({ ...f, deadline: e.target.value }))} />
          </div>
          {err && <p className="text-sm text-red-600">{err}</p>}
        </CardContent>
        <div className="flex gap-3 px-5 pb-5">
          <Button className="flex-1" onClick={submit} disabled={loading || !form.product_name || !form.price_per_unit || !form.quantity}>
            {loading ? 'Создание...' : 'Создать сделку'}
          </Button>
          <Button variant="ghost" onClick={onClose}>Отмена</Button>
        </div>
      </Card>
    </div>
  )
}

function SupplierCard({ s }: { s: SupplierEntry }) {
  const [expanded, setExpanded] = useState(false)
  const [reviews, setReviews] = useState<SupplierReviewEntry[]>([])
  const [showReviewForm, setShowReviewForm] = useState(false)
  const [showDealForm, setShowDealForm] = useState(false)
  const [loadingReviews, setLoadingReviews] = useState(false)

  async function loadReviews() {
    if (reviews.length > 0) return
    setLoadingReviews(true)
    try {
      const data = await api.supplierReviews.list('supplier', s.id)
      setReviews(data)
    } catch { /* ignore */ } finally { setLoadingReviews(false) }
  }

  function toggleExpand() {
    if (!expanded) loadReviews()
    setExpanded(v => !v)
  }

  return (
    <Card className={`shadow-stripe transition-all ${expanded ? 'border-primary/30' : 'border-border/60'}`}>
      <CardContent className="p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="font-semibold truncate" style={{ color: '#FFFFFF' }}>{s.company_name}</h3>
              {s.is_verified && (
                <Badge variant="outline" className="text-xs px-2 h-5 gap-1" style={{ color: 'var(--violet)', borderColor: 'rgba(110,106,252,0.3)', background: 'rgba(110,106,252,0.06)' }}>
                  <ShieldCheck size={10} /> Верифицирован
                </Badge>
              )}
              <Badge variant="secondary" className="text-xs px-2 h-5">{s.country === 'china' ? 'Китай' : 'Россия'}</Badge>
            </div>
            <div className="flex items-center gap-3 mt-1 flex-wrap">
              <Stars rating={s.rating} />
              <span className="text-xs text-muted-foreground">{s.total_reviews} отзывов · {s.total_deals} сделок</span>
              {s.industry && (
                <Badge variant="outline" className="text-xs px-2 h-5" style={{ color: 'var(--violet)', borderColor: 'rgba(110,106,252,0.2)', background: 'rgba(110,106,252,0.05)' }}>{s.industry}</Badge>
              )}
              {s.region && <span className="text-xs text-muted-foreground">{s.region}</span>}
            </div>
            {s.description && <p className="text-sm mt-2 line-clamp-2 text-muted-foreground">{s.description}</p>}
          </div>
          <div className="flex flex-col gap-2 shrink-0">
            <Button size="sm" onClick={() => setShowDealForm(true)} className="text-xs whitespace-nowrap">Оформить сделку</Button>
            <Button size="sm" variant="outline" onClick={toggleExpand} className="text-xs whitespace-nowrap gap-1">
              {expanded ? <><ChevronUp size={12} />Скрыть</> : <><ChevronDown size={12} />Подробнее</>}
            </Button>
          </div>
        </div>

        {s.min_order_qty && (
          <div className="flex items-center gap-1 mt-2 text-xs text-muted-foreground">
            <Package size={11} />
            <span>Мин. заказ: {s.min_order_qty} ед.</span>
          </div>
        )}

        {expanded && (
          <div className="mt-4 pt-4 border-t border-border space-y-4">
            <div className="flex flex-wrap gap-4 text-sm">
              {s.website && (
                <a href={`https://${s.website}`} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 hover:opacity-80 transition-opacity" style={{ color: 'var(--violet)' }}>
                  <Globe size={14} /> {s.website}
                </a>
              )}
              {s.phone && (
                <span className="flex items-center gap-1.5 text-muted-foreground">
                  <Phone size={14} /> {s.phone}
                </span>
              )}
            </div>

            {s.industry && PRODUCTS_BY_INDUSTRY[s.industry] && (
              <div>
                <p className="text-sm font-medium mb-3" style={{ color: '#FFFFFF' }}>Ассортимент товаров</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {PRODUCTS_BY_INDUSTRY[s.industry]!.map((p, i) => (
                    <div key={i} className="flex items-center gap-3 rounded-xl p-3 bg-muted/50 border border-border/60">
                      <div className="w-10 h-10 rounded-xl flex items-center justify-center text-xl shrink-0" style={{ background: 'rgba(110,106,252,0.07)', border: '1px solid rgba(110,106,252,0.12)' }}>
                        {p.emoji}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-xs font-medium truncate" style={{ color: '#FFFFFF' }}>{p.name}</p>
                        <p className="text-xs font-bold" style={{ color: 'var(--violet)' }}>{p.price.toLocaleString('ru-RU')} ₽</p>
                        <p className="text-[11px] text-muted-foreground">мин. партия: {p.minQty.toLocaleString('ru-RU')} шт.</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div>
              <div className="flex items-center justify-between mb-2">
                <p className="text-sm font-medium" style={{ color: '#FFFFFF' }}>Отзывы</p>
                {!showReviewForm && (
                  <button onClick={() => setShowReviewForm(true)} className="text-xs transition-opacity hover:opacity-70" style={{ color: 'var(--violet)' }}>
                    + Написать отзыв
                  </button>
                )}
              </div>
              {loadingReviews && <p className="text-xs text-muted-foreground">Загрузка...</p>}
              {!loadingReviews && reviews.length === 0 && !showReviewForm && (
                <p className="text-xs text-muted-foreground">Пока нет отзывов</p>
              )}
              {reviews.map(r => (
                <div key={r.id} className="rounded-lg p-3 mb-2 bg-muted/40 border border-border/60">
                  <div className="flex items-center gap-2 mb-1">
                    <Stars rating={r.rating} />
                    <span className="text-xs text-muted-foreground">{new Date(r.created_at).toLocaleDateString('ru-RU')}</span>
                  </div>
                  {r.text && <p className="text-sm text-muted-foreground">{r.text}</p>}
                </div>
              ))}
              {showReviewForm && (
                <ReviewForm supplierId={s.id} onDone={() => {
                  setShowReviewForm(false)
                  setReviews([])
                  loadReviews()
                }} />
              )}
            </div>
          </div>
        )}
      </CardContent>

      {showDealForm && <DealForm supplierId={s.id} onClose={() => setShowDealForm(false)} />}
    </Card>
  )
}

export default function SuppliersPage() {
  const [suppliers, setSuppliers] = useState<SupplierEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [industry, setIndustry] = useState('')
  const [country, setCountry] = useState('')
  const [sort, setSort] = useState('rating')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.catalog.listSuppliers({ industry, country, sort, search: search || undefined })
      setSuppliers(data)
    } catch { /* backend offline: show empty state */ } finally { setLoading(false) }
  }, [industry, country, sort, search])

  useEffect(() => { load() }, [load])

  return (
    <AppShell>
      <div className="flex-1 p-4 md:p-6" style={{ background: 'var(--bg)' }}>
      <div className="max-w-5xl mx-auto">
        <BlurFade inView className="mb-6">
          <div className="flex items-center gap-3">
            <div className="w-11 h-11 rounded-2xl flex items-center justify-center shadow-stripe" style={{ background: 'var(--violet)' }}>
              <Package size={20} style={{ color: 'white' }} />
            </div>
            <div>
              <h1 className="text-2xl font-bold" style={{ color: '#FFFFFF', letterSpacing: '-0.02em' }}>Каталог поставщиков</h1>
              <p className="text-sm text-muted-foreground mt-0.5">Найдите производителей для ваших товаров</p>
            </div>
          </div>
        </BlurFade>

        {/* Filters */}
        <BlurFade inView delay={0.05}>
          <Card className="shadow-stripe mb-5">
            <CardContent className="p-4">
              <div className="flex flex-wrap gap-3 items-center">
                <div className="flex items-center gap-2 rounded-lg border border-border bg-background px-3 py-2 flex-1 min-w-[200px] focus-within:ring-2 focus-within:ring-primary/20 focus-within:border-primary/40 transition-all">
                  <Search size={15} className="text-muted-foreground shrink-0" />
                  <input
                    className="flex-1 text-sm outline-none bg-transparent text-foreground placeholder:text-muted-foreground"
                    placeholder="Поиск по названию или описанию..."
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                  />
                  {search && (
                    <button onClick={() => setSearch('')} className="text-muted-foreground hover:text-foreground transition-colors">
                      <X size={14} />
                    </button>
                  )}
                </div>
                <select value={industry} onChange={e => setIndustry(e.target.value)} className="rounded-lg border border-border bg-background text-sm px-3 py-2 outline-none focus:ring-2 focus:ring-primary/20 text-foreground">
                  {INDUSTRIES.map(i => <option key={i.value} value={i.value}>{i.label}</option>)}
                </select>
                <select value={country} onChange={e => setCountry(e.target.value)} className="rounded-lg border border-border bg-background text-sm px-3 py-2 outline-none focus:ring-2 focus:ring-primary/20 text-foreground">
                  {COUNTRIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
                <select value={sort} onChange={e => setSort(e.target.value)} className="rounded-lg border border-border bg-background text-sm px-3 py-2 outline-none focus:ring-2 focus:ring-primary/20 text-foreground">
                  <option value="rating">По рейтингу</option>
                  <option value="deals">По сделкам</option>
                  <option value="reviews">По отзывам</option>
                  <option value="name">По названию</option>
                </select>
              </div>
            </CardContent>
          </Card>
        </BlurFade>

        {/* Results */}
        {loading ? (
          <div className="space-y-4">
            {[1,2,3].map(i => <Skeleton key={i} className="h-36 rounded-xl" />)}
          </div>
        ) : suppliers.length === 0 ? (
          <Card className="shadow-stripe">
            <CardContent className="p-12 text-center">
              <Building2 size={36} className="mx-auto mb-3 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">Поставщики не найдены. Попробуйте изменить фильтры.</p>
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">Найдено: {suppliers.length}</p>
            {suppliers.map((s, idx) => (
              <BlurFade key={s.id} inView delay={idx * 0.05}>
                <SupplierCard s={s} />
              </BlurFade>
            ))}
          </div>
        )}
      </div>
      </div>
    </AppShell>
  )
}
