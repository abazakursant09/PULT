'use client'

import { useEffect, useState, useCallback } from 'react'
import { api, PromoEntry, PromoStats } from '@/lib/api'
import { Tag, Plus, ToggleLeft, ToggleRight, TrendingUp, Users, Percent, Clock, Minus, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table'

const TYPE_OPTIONS = [
  { value: 'percent',        label: 'Скидка %' },
  { value: 'fixed',          label: 'Фиксированная скидка (₽)' },
  { value: 'extended_trial', label: 'Продлённый пробный период' },
  { value: 'blogger_free',   label: 'Бесплатный тариф (дни)' },
]

const TYPE_ICON: Record<string, React.ReactNode> = {
  percent:        <Percent className="w-3.5 h-3.5" />,
  fixed:          <Minus className="w-3.5 h-3.5" />,
  extended_trial: <Clock className="w-3.5 h-3.5" />,
  blogger_free:   <Sparkles className="w-3.5 h-3.5" />,
}

const TYPE_LABEL: Record<string, string> = {
  percent:        'Скидка %',
  fixed:          'Скидка ₽',
  extended_trial: 'Пробный период',
  blogger_free:   'Бесплатный тариф',
}

function CreateModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState({
    code: '', type: 'percent', value: '', description: '',
    applicable_plans: 'all', max_activations: '', blogger_name: '', expires_at: '',
  })
  const [loading, setLoading] = useState(false)
  const [err, setErr]         = useState('')

  const set = (k: string, v: string) => setForm(f => ({ ...f, [k]: v }))

  async function submit() {
    setLoading(true); setErr('')
    try {
      await api.promo.adminCreate({
        code: form.code,
        type: form.type,
        value: parseFloat(form.value),
        description: form.description || undefined,
        applicable_plans: form.applicable_plans,
        max_activations: form.max_activations ? parseInt(form.max_activations) : undefined,
        blogger_name: form.blogger_name || undefined,
        expires_at:   form.expires_at   || undefined,
      })
      onCreated()
      onClose()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Ошибка')
    } finally { setLoading(false) }
  }

  const valueLabel = form.type === 'percent' ? 'Процент (%)' :
                     form.type === 'fixed'   ? 'Сумма (₽)' : 'Дней'

  return (
    <Dialog open={open} onOpenChange={v => !v && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Создать промокод</DialogTitle>
        </DialogHeader>

        <div className="space-y-3 max-h-[60vh] overflow-y-auto pr-1">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="promo-code">Код *</Label>
              <Input
                id="promo-code"
                className="font-mono uppercase"
                placeholder="PROMO2024"
                value={form.code}
                onChange={e => set('code', e.target.value.toUpperCase())}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="promo-type">Тип *</Label>
              <Select
                id="promo-type"
                value={form.type}
                onChange={e => set('type', e.target.value)}
              >
                {TYPE_OPTIONS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="promo-value">{valueLabel} *</Label>
              <Input
                id="promo-value"
                type="number"
                value={form.value}
                onChange={e => set('value', e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="promo-max">Макс. активаций</Label>
              <Input
                id="promo-max"
                type="number"
                placeholder="∞"
                value={form.max_activations}
                onChange={e => set('max_activations', e.target.value)}
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="promo-desc">Описание</Label>
            <Input
              id="promo-desc"
              value={form.description}
              onChange={e => set('description', e.target.value)}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="promo-plans">Тарифы</Label>
              <Select
                id="promo-plans"
                value={form.applicable_plans}
                onChange={e => set('applicable_plans', e.target.value)}
              >
                <option value="all">Все</option>
                <option value="master">Мастер</option>
                <option value="profi">Профи</option>
                <option value="maximum">Максимум</option>
                <option value="profi,maximum">Профи + Максимум</option>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="promo-expires">Истекает</Label>
              <Input
                id="promo-expires"
                type="datetime-local"
                value={form.expires_at}
                onChange={e => set('expires_at', e.target.value)}
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="promo-blogger">Блогер (имя)</Label>
            <Input
              id="promo-blogger"
              placeholder="Имя Фамилия"
              value={form.blogger_name}
              onChange={e => set('blogger_name', e.target.value)}
            />
          </div>

          {err && <p className="text-destructive text-sm">{err}</p>}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Отмена</Button>
          <Button onClick={submit} loading={loading} disabled={!form.code || !form.value}>
            Создать
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function StatCard({ label, value, sub, icon }: { label: string; value: string | number; sub?: string; icon: React.ReactNode }) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-start gap-3">
          <div className="p-2 bg-[rgba(26,115,232,0.08)] rounded-lg shrink-0 text-primary">{icon}</div>
          <div>
            <p className="text-2xl font-bold text-foreground">{value}</p>
            <p className="text-sm text-muted-foreground">{label}</p>
            {sub && <p className="text-xs text-muted-foreground/60 mt-0.5">{sub}</p>}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

export default function AdminPromocodesPage() {
  const [promos,     setPromos]     = useState<PromoEntry[]>([])
  const [stats,      setStats]      = useState<PromoStats | null>(null)
  const [loading,    setLoading]    = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [tab,        setTab]        = useState<'all' | 'active'>('all')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [p, s] = await Promise.all([api.promo.adminList(), api.promo.adminStats()])
      setPromos(p)
      setStats(s)
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  async function toggle(id: string) {
    await api.promo.adminToggle(id)
    load()
  }

  const displayed = tab === 'active' ? promos.filter(p => p.is_active) : promos

  return (
    <div className="min-h-screen bg-background p-4 md:p-6">
      <div className="max-w-6xl mx-auto space-y-6">

        {/* Header */}
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <h1 className="text-2xl font-bold text-foreground">Управление промокодами</h1>
            <p className="text-sm text-muted-foreground mt-0.5">Создавайте и отслеживайте промокоды и партнёрские коды</p>
          </div>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="w-4 h-4" /> Создать промокод
          </Button>
        </div>

        {/* Stats */}
        {stats && (
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard label="Всего промокодов"  value={stats.total_promos}      icon={<Tag className="w-5 h-5" />} />
            <StatCard label="Активных"          value={stats.active_promos}     icon={<ToggleRight className="w-5 h-5" />} />
            <StatCard label="Активаций"         value={stats.total_activations} icon={<TrendingUp className="w-5 h-5" />} />
            <StatCard label="Блогеров"          value={stats.bloggers.length}   icon={<Users className="w-5 h-5" />} />
          </div>
        )}

        {/* Blogger stats */}
        {stats && stats.bloggers.length > 0 && (
          <Card>
            <div className="px-5 py-3 border-b border-border flex items-center gap-2">
              <Users className="w-4 h-4 text-primary" />
              <h2 className="font-semibold text-sm text-foreground">Статистика по блогерам</h2>
            </div>
            <div className="divide-y divide-border">
              {stats.bloggers.map(b => (
                <div key={b.blogger_name} className="px-5 py-3 flex items-center justify-between gap-4 flex-wrap hover:bg-muted/30 transition-colors">
                  <div>
                    <p className="font-medium text-foreground">{b.blogger_name}</p>
                    <div className="flex gap-2 mt-1 flex-wrap">
                      {b.codes.map(c => (
                        <Badge key={c} variant="outline" className="font-mono text-xs">{c}</Badge>
                      ))}
                    </div>
                  </div>
                  <div className="flex gap-6 text-sm">
                    <div className="text-center">
                      <p className="text-xl font-bold text-foreground">{b.total_activations}</p>
                      <p className="text-xs text-muted-foreground">активаций</p>
                    </div>
                    <div className="text-center">
                      <p className="text-xl font-bold text-foreground">{b.total_codes}</p>
                      <p className="text-xs text-muted-foreground">кодов</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        )}

        {/* Table */}
        <Card>
          <div className="flex items-center justify-between px-5 py-3 border-b border-border flex-wrap gap-2">
            <div className="flex gap-1">
              {(['all', 'active'] as const).map(t => (
                <Button
                  key={t}
                  variant={tab === t ? 'default' : 'ghost'}
                  size="sm"
                  onClick={() => setTab(t)}
                >
                  {t === 'all' ? `Все (${promos.length})` : `Активные (${promos.filter(p => p.is_active).length})`}
                </Button>
              ))}
            </div>
          </div>

          {loading ? (
            <div className="p-8 text-center text-muted-foreground">Загрузка...</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Код</TableHead>
                  <TableHead>Тип</TableHead>
                  <TableHead>Значение</TableHead>
                  <TableHead>Тарифы</TableHead>
                  <TableHead>Активаций</TableHead>
                  <TableHead>Блогер</TableHead>
                  <TableHead>Истекает</TableHead>
                  <TableHead className="text-center">Статус</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {displayed.map(p => (
                  <TableRow key={p.id} className={!p.is_active ? 'opacity-50' : ''}>
                    <TableCell>
                      <span className="font-mono font-semibold text-primary">{p.code}</span>
                      {p.description && (
                        <p className="text-xs text-muted-foreground mt-0.5 max-w-[200px] truncate">{p.description}</p>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="gap-1">
                        {TYPE_ICON[p.type]} {TYPE_LABEL[p.type] ?? p.type}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-medium text-foreground">
                      {p.type === 'percent' ? `${p.value}%` :
                       p.type === 'fixed'   ? `${p.value} ₽` : `${p.value} дн.`}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs">
                        {p.applicable_plans === 'all' ? 'Все' : p.applicable_plans}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <span className="font-medium text-foreground">{p.current_activations}</span>
                      {p.max_activations && (
                        <span className="text-muted-foreground"> / {p.max_activations}</span>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {p.blogger_name ?? '—'}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {p.expires_at
                        ? new Date(p.expires_at).toLocaleDateString('ru-RU')
                        : '∞'}
                    </TableCell>
                    <TableCell className="text-center">
                      <button
                        onClick={() => toggle(p.id)}
                        className={`transition-colors ${p.is_active ? 'text-primary hover:opacity-70' : 'text-muted-foreground hover:text-foreground'}`}
                        title={p.is_active ? 'Деактивировать' : 'Активировать'}
                      >
                        {p.is_active
                          ? <ToggleRight className="w-6 h-6" />
                          : <ToggleLeft  className="w-6 h-6" />}
                      </button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

          {!loading && displayed.length === 0 && (
            <div className="py-12 text-center text-muted-foreground">
              <Tag className="w-10 h-10 mx-auto mb-2 opacity-30" />
              <p>Нет промокодов</p>
            </div>
          )}
        </Card>
      </div>

      <CreateModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={load}
      />
    </div>
  )
}
