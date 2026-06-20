'use client'

import { useEffect, useState } from 'react'
import {
  ShieldCheck, ShieldX, Clock, AlertTriangle, Check,
  RefreshCw, Trash2, Globe, Phone, Building2, Hash,
  ChevronDown, ChevronUp,
} from 'lucide-react'
import { api, type SupplierVerification, type SupplierVerificationCreate } from '@/lib/api'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Textarea } from '@/components/ui/textarea'

const A   = '#3B82F6'
const ABG = 'rgba(26,115,232,0.08)'
const ABR = 'rgba(26,115,232,0.18)'

const STATUS_CONFIG = {
  verified: { label: 'Верифицирован', icon: <ShieldCheck size={15} />, variant: 'outline' as const, color: '#1A73E8', borderColor: 'rgba(26,115,232,0.22)', bg: 'rgba(26,115,232,0.08)' },
  pending:  { label: 'На проверке',  icon: <Clock size={15} />,       variant: 'warning' as const,  color: 'var(--warning)', borderColor: 'rgba(245,158,11,0.2)',  bg: 'rgba(245,158,11,0.08)' },
  rejected: { label: 'Отклонена',    icon: <ShieldX size={15} />,     variant: 'destructive' as const, color: '#DC2626', borderColor: 'rgba(220,38,38,0.2)', bg: 'rgba(220,38,38,0.08)' },
} as const

function StatusBadge({ status }: { status: string }) {
  const cfg = STATUS_CONFIG[status as keyof typeof STATUS_CONFIG] ?? STATUS_CONFIG.pending
  return (
    <span
      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold"
      style={{ background: cfg.bg, border: `1px solid ${cfg.borderColor}`, color: cfg.color }}
    >
      {cfg.icon}
      {cfg.label}
    </span>
  )
}

function VerifiedBadge() {
  return (
    <Badge variant="outline" className="gap-1.5" style={{ background: 'rgba(26,115,232,0.1)', color: '#1A73E8', borderColor: 'rgba(26,115,232,0.25)' }}>
      <ShieldCheck size={13} />
      Проверенный производитель
    </Badge>
  )
}

function fmt(iso: string) {
  return new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })
}

const SOURCE_LABEL: Record<string, string> = {
  fns:    'ФНС',
  '2gis': '2ГИС',
  uscc:   'USCC (Китай)',
  manual: 'Ручная проверка',
}

const REJECTION_LABEL: Record<string, string> = {
  inn_missing:    'ИНН не указан',
  inn_format:     'Неверный формат ИНН',
  inn_length:     'Неверная длина ИНН',
  inn_not_found:  'ИНН не найден в реестре ФНС',
  address_missing:'Адрес слишком короткий или не указан',
  manual_required:'Требуется ручная проверка адреса',
  uscc_missing:   'USCC не указан',
  uscc_format:    'Неверный формат USCC',
  scope_missing:  'Business Scope не указан',
  scope_mismatch: 'Business Scope не содержит «manufacturing»',
  company_too_new:'Компания существует менее 1 года',
}

function ApplicationCard({ sv, onRevoke }: { sv: SupplierVerification; onRevoke: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const [revoking, setRevoking] = useState(false)

  async function handleRevoke() {
    if (!confirm('Отозвать заявку? Вы сможете подать новую.')) return
    setRevoking(true)
    try {
      await api.suppliers.revoke(sv.id)
      onRevoke()
    } catch {}
    finally { setRevoking(false) }
  }

  const cfg = STATUS_CONFIG[sv.status as keyof typeof STATUS_CONFIG] ?? STATUS_CONFIG.pending

  return (
    <Card className="p-6" style={{ borderColor: cfg.borderColor }}>
      <div className="flex items-start justify-between gap-4 flex-wrap mb-4">
        <div>
          <div className="flex items-center gap-3 flex-wrap mb-1">
            <h2 className="font-bold text-lg" style={{ color: '#202124' }}>{sv.company_name}</h2>
            <StatusBadge status={sv.status} />
            {sv.status === 'verified' && <VerifiedBadge />}
          </div>
          <p className="text-xs" style={{ color: 'rgba(0,0,0,0.38)' }}>
            Подана {fmt(sv.created_at)}
            {sv.verified_at && ` · Верифицирована ${fmt(sv.verified_at)}`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => setExpanded(v => !v)}>
            {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            {expanded ? 'Скрыть' : 'Детали'}
          </Button>
          {sv.status !== 'verified' && (
            <Button variant="ghost" size="sm" onClick={handleRevoke} loading={revoking}
                    style={{ color: '#DC2626' }}>
              {!revoking && <><Trash2 size={13} /> Отозвать</>}
            </Button>
          )}
        </div>
      </div>

      {sv.status === 'rejected' && sv.rejection_reason && (
        <div className="flex items-start gap-2 px-4 py-3 rounded-xl mb-4 text-sm"
             style={{ background: 'rgba(220,38,38,0.05)', border: '1px solid rgba(220,38,38,0.15)' }}>
          <AlertTriangle size={14} className="shrink-0 mt-0.5" style={{ color: '#DC2626' }} />
          <span style={{ color: '#DC2626' }}>{REJECTION_LABEL[sv.rejection_reason] ?? sv.rejection_reason}</span>
        </div>
      )}

      {expanded && (
        <div className="space-y-2 pt-2 animate-fade-in" style={{ borderTop: '1px solid rgba(26,115,232,0.1)' }}>
          {sv.inn           && <Row icon={<Hash size={12} />}      label="ИНН"             val={sv.inn} />}
          {sv.ogrn          && <Row icon={<Hash size={12} />}      label="ОГРН"            val={sv.ogrn} />}
          {sv.legal_address && <Row icon={<Building2 size={12} />} label="Адрес"           val={sv.legal_address} />}
          {sv.phone         && <Row icon={<Phone size={12} />}     label="Телефон"         val={sv.phone} />}
          {sv.website       && <Row icon={<Globe size={12} />}     label="Сайт"            val={sv.website} />}
          {sv.uscc          && <Row icon={<Hash size={12} />}      label="USCC"            val={sv.uscc} />}
          {sv.business_scope && <Row icon={<Building2 size={12} />} label="Business Scope" val={sv.business_scope} />}
          {sv.founded_year  && <Row icon={<Clock size={12} />}     label="Год основания"   val={String(sv.founded_year)} />}
          {sv.verification_source && (
            <Row icon={<Check size={12} />} label="Источник проверки"
                 val={SOURCE_LABEL[sv.verification_source] ?? sv.verification_source} />
          )}
        </div>
      )}
    </Card>
  )
}

function Row({ icon, label, val }: { icon: React.ReactNode; label: string; val: string }) {
  return (
    <div className="flex items-start gap-3 py-2" style={{ borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
      <span className="shrink-0 mt-0.5" style={{ color: 'rgba(0,0,0,0.38)' }}>{icon}</span>
      <span className="text-xs font-medium shrink-0 w-28" style={{ color: 'rgba(0,0,0,0.38)' }}>{label}</span>
      <span className="text-xs" style={{ color: '#202124', wordBreak: 'break-word' }}>{val}</span>
    </div>
  )
}

const EMPTY_RUSSIA: SupplierVerificationCreate = {
  company_name: '', country: 'russia',
  inn: '', ogrn: '', legal_address: '', phone: '', website: '',
}
const EMPTY_CHINA: SupplierVerificationCreate = {
  company_name: '', country: 'china',
  uscc: '', business_scope: '', founded_year: undefined,
}

export default function SupplierVerifyPage() {
  const [myApps,   setMyApps]   = useState<SupplierVerification[]>([])
  const [loading,  setLoading]  = useState(true)
  const [country,  setCountry]  = useState<'russia' | 'china'>('russia')
  const [form,     setForm]     = useState<SupplierVerificationCreate>(EMPTY_RUSSIA)
  const [submitting, setSubmitting] = useState(false)
  const [error,    setError]    = useState('')
  const [showForm, setShowForm] = useState(false)

  const hasActive = myApps.some(a => a.status === 'pending' || a.status === 'verified')

  useEffect(() => { loadMy() }, [])

  async function loadMy() {
    setLoading(true)
    try { setMyApps(await api.suppliers.my()) } catch {}
    finally { setLoading(false) }
  }

  function switchCountry(c: 'russia' | 'china') {
    setCountry(c)
    setForm(c === 'china' ? { ...EMPTY_CHINA } : { ...EMPTY_RUSSIA })
    setError('')
  }

  function setF<K extends keyof SupplierVerificationCreate>(k: K, v: SupplierVerificationCreate[K]) {
    setForm(f => ({ ...f, [k]: v }))
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(''); setSubmitting(true)
    try {
      const sv = await api.suppliers.submit({ ...form, country })
      setMyApps(prev => [sv, ...prev])
      setShowForm(false)
      setForm(country === 'china' ? { ...EMPTY_CHINA } : { ...EMPTY_RUSSIA })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Ошибка подачи заявки')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="max-w-2xl mx-auto px-5 py-10 space-y-8">

      <Card className="p-6 sm:p-8">
        <div className="flex items-center gap-4 mb-4">
          <div className="w-11 h-11 rounded-2xl flex items-center justify-center shrink-0"
               style={{ background: ABG, border: `1px solid ${ABR}` }}>
            <ShieldCheck size={20} style={{ color: A }} />
          </div>
          <div>
            <h1 className="font-bold" style={{ fontSize: '1.375rem', color: '#202124' }}>
              Верификация производителей
            </h1>
            <p className="text-sm mt-0.5" style={{ color: 'rgba(0,0,0,0.38)' }}>
              Подтвердите легальность компании через ФНС, 2ГИС или USCC
            </p>
          </div>
        </div>
        <p style={{ fontSize: '0.9375rem', color: '#5F6368', lineHeight: 1.7 }}>
          Верифицированные производители получают значок
          {' '}<strong style={{ color: '#1A73E8' }}>«Проверенный производитель»</strong>,
          который отображается в карточке компании и повышает доверие покупателей.
        </p>
      </Card>

      {loading ? (
        <div className="flex justify-center py-10">
          <RefreshCw size={24} className="animate-spin text-muted-foreground" />
        </div>
      ) : (
        <>
          {myApps.length > 0 && (
            <div className="space-y-4">
              <h2 className="font-semibold text-sm" style={{ color: '#5F6368' }}>
                Мои заявки ({myApps.length})
              </h2>
              {myApps.map(sv => (
                <ApplicationCard key={sv.id} sv={sv} onRevoke={loadMy} />
              ))}
            </div>
          )}

          {!hasActive && (
            <div>
              {!showForm ? (
                <Button className="w-full" size="lg" onClick={() => setShowForm(true)}>
                  <ShieldCheck size={16} /> Подать заявку на верификацию
                </Button>
              ) : (
                <Card className="p-6 sm:p-8 animate-fade-in">
                  <h2 className="font-semibold mb-6" style={{ fontSize: '1.0625rem', color: '#202124' }}>
                    Новая заявка
                  </h2>

                  <div className="mb-6">
                    <Label className="mb-2 block">Страна регистрации</Label>
                    <div className="flex gap-2">
                      {(['russia', 'china'] as const).map(c => (
                        <button
                          key={c}
                          type="button"
                          onClick={() => switchCountry(c)}
                          className="flex-1 py-2.5 rounded-xl text-sm font-semibold transition-all duration-150"
                          style={{
                            background: country === c ? A : 'rgba(0,0,0,0.04)',
                            color:      country === c ? '#F8F9FA' : '#8A8986',
                            border:     `1px solid ${country === c ? A : 'rgba(26,115,232,0.12)'}`,
                          }}
                        >
                          {c === 'russia' ? '🇷🇺 Россия' : '🇨🇳 Китай'}
                        </button>
                      ))}
                    </div>
                  </div>

                  {error && (
                    <div className="mb-5 px-4 py-3 rounded-xl text-sm"
                         style={{ background: 'rgba(220,38,38,0.06)', border: '1px solid rgba(220,38,38,0.2)', color: '#DC2626' }}>
                      {error}
                    </div>
                  )}

                  <form onSubmit={submit} className="space-y-5">
                    <div className="space-y-2">
                      <Label htmlFor="company_name">Название компании *</Label>
                      <Input id="company_name" required
                        placeholder={country === 'china' ? 'Shenzhen XYZ Technology Co., Ltd.' : 'ООО «Ромашка»'}
                        value={form.company_name}
                        onChange={e => setF('company_name', e.target.value)} />
                    </div>

                    {country === 'russia' ? (
                      <>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                          <div className="space-y-2">
                            <Label htmlFor="inn">ИНН *</Label>
                            <Input id="inn" required placeholder="7700000000"
                              value={form.inn ?? ''}
                              onChange={e => setF('inn', e.target.value)} />
                            <p className="text-xs" style={{ color: 'rgba(0,0,0,0.38)' }}>10 цифр (юрлицо) или 12 (ИП)</p>
                          </div>
                          <div className="space-y-2">
                            <Label htmlFor="ogrn">ОГРН</Label>
                            <Input id="ogrn" placeholder="1027700000000"
                              value={form.ogrn ?? ''}
                              onChange={e => setF('ogrn', e.target.value)} />
                          </div>
                        </div>
                        <div className="space-y-2">
                          <Label htmlFor="legal_address">Юридический адрес</Label>
                          <Input id="legal_address"
                            placeholder="г. Москва, ул. Примерная, д. 1, оф. 10"
                            value={form.legal_address ?? ''}
                            onChange={e => setF('legal_address', e.target.value)} />
                        </div>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                          <div className="space-y-2">
                            <Label htmlFor="phone">Телефон</Label>
                            <Input id="phone" placeholder="+7 (999) 000-00-00"
                              value={form.phone ?? ''}
                              onChange={e => setF('phone', e.target.value)} />
                          </div>
                          <div className="space-y-2">
                            <Label htmlFor="website">Сайт</Label>
                            <Input id="website" placeholder="https://example.ru"
                              value={form.website ?? ''}
                              onChange={e => setF('website', e.target.value)} />
                          </div>
                        </div>
                        <div className="px-4 py-3 rounded-xl text-xs"
                             style={{ background: ABG, border: `1px solid ${ABR}`, color: '#5F6368' }}>
                          Проверка выполняется по базе <strong style={{ color: A }}>ФНС</strong> (ИНН) и{' '}
                          <strong style={{ color: A }}>2ГИС</strong> (адрес). Результат — мгновенно.
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="space-y-2">
                          <Label htmlFor="uscc">USCC — Unified Social Credit Code *</Label>
                          <Input id="uscc" required placeholder="91310000MA1FL0XX00"
                            maxLength={18} className="font-mono"
                            value={form.uscc ?? ''}
                            onChange={e => setF('uscc', e.target.value.toUpperCase())} />
                          <p className="text-xs" style={{ color: 'rgba(0,0,0,0.38)' }}>
                            18 символов, латиница и цифры (без I, O, S, V, Z)
                          </p>
                        </div>
                        <div className="space-y-2">
                          <Label htmlFor="business_scope">Business Scope *</Label>
                          <Textarea id="business_scope" required rows={3}
                            placeholder="Manufacturing and sales of electronic products..."
                            value={form.business_scope ?? ''}
                            onChange={e => setF('business_scope', e.target.value)} />
                          <p className="text-xs" style={{ color: 'rgba(0,0,0,0.38)' }}>
                            Должно содержать «manufacturing» или «production»
                          </p>
                        </div>
                        <div className="space-y-2">
                          <Label htmlFor="founded_year">Год основания компании</Label>
                          <Input id="founded_year" type="number" min={1900} max={new Date().getFullYear()}
                            placeholder={String(new Date().getFullYear() - 3)}
                            value={form.founded_year ?? ''}
                            onChange={e => setF('founded_year', e.target.value ? Number(e.target.value) : undefined)} />
                          <p className="text-xs" style={{ color: 'rgba(0,0,0,0.38)' }}>
                            Компания должна существовать не менее 1 года
                          </p>
                        </div>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                          <div className="space-y-2">
                            <Label htmlFor="phone_cn">Телефон</Label>
                            <Input id="phone_cn" placeholder="+86 XXX XXXX XXXX"
                              value={form.phone ?? ''}
                              onChange={e => setF('phone', e.target.value)} />
                          </div>
                          <div className="space-y-2">
                            <Label htmlFor="website_cn">Сайт</Label>
                            <Input id="website_cn" placeholder="https://example.cn"
                              value={form.website ?? ''}
                              onChange={e => setF('website', e.target.value)} />
                          </div>
                        </div>
                        <div className="px-4 py-3 rounded-xl text-xs"
                             style={{ background: ABG, border: `1px solid ${ABR}`, color: '#5F6368' }}>
                          Проверка выполняется по коду <strong style={{ color: A }}>USCC</strong>,
                          сфере деятельности и сроку существования компании.
                        </div>
                      </>
                    )}

                    <div className="flex gap-3 pt-2">
                      <Button type="submit" loading={submitting}>
                        {!submitting && <><ShieldCheck size={15} /> Подать заявку</>}
                      </Button>
                      <Button type="button" variant="ghost"
                              onClick={() => { setShowForm(false); setError('') }}>
                        Отмена
                      </Button>
                    </div>
                  </form>
                </Card>
              )}
            </div>
          )}

          {hasActive && !showForm && (
            <div className="px-4 py-3 rounded-xl text-sm"
                 style={{ background: ABG, border: `1px solid ${ABR}`, color: '#5F6368' }}>
              У вас уже есть активная заявка. Отзовите её, чтобы подать повторно.
            </div>
          )}
        </>
      )}

      <VerifiedList />
    </main>
  )
}

function VerifiedList() {
  const [list,    setList]    = useState<SupplierVerification[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.suppliers.listVerified()
      .then(setList)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  return (
    <Card className="p-6 sm:p-8">
      <h2 className="font-semibold mb-5" style={{ fontSize: '1.0625rem', color: '#202124' }}>
        Реестр проверенных производителей
      </h2>

      {loading ? (
        <div className="flex justify-center py-6">
          <RefreshCw size={20} className="animate-spin text-muted-foreground" />
        </div>
      ) : list.length === 0 ? (
        <p className="text-sm text-center py-6" style={{ color: 'rgba(0,0,0,0.38)' }}>
          Верифицированных производителей пока нет
        </p>
      ) : (
        <div className="space-y-3">
          {list.map(sv => (
            <div
              key={sv.id}
              className="flex items-center justify-between gap-4 px-4 py-3.5 rounded-xl"
              style={{ background: 'rgba(26,115,232,0.05)', border: '1px solid rgba(26,115,232,0.15)' }}
            >
              <div className="flex items-center gap-3 min-w-0">
                <ShieldCheck size={16} style={{ color: '#1A73E8', flexShrink: 0 }} />
                <div className="min-w-0">
                  <p className="font-semibold text-sm truncate" style={{ color: '#202124' }}>
                    {sv.company_name}
                  </p>
                  <p className="text-xs" style={{ color: 'rgba(0,0,0,0.38)' }}>
                    {sv.country === 'china' ? '🇨🇳 Китай' : '🇷🇺 Россия'}
                    {sv.inn && ` · ИНН ${sv.inn}`}
                    {sv.uscc && ` · USCC ${sv.uscc.slice(0, 8)}…`}
                    {sv.verified_at && ` · ${fmt(sv.verified_at)}`}
                  </p>
                </div>
              </div>
              <VerifiedBadge />
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}
