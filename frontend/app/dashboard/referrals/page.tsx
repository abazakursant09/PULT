'use client'

import React, { useState, useEffect } from 'react'
import {
  Gift, Copy, Check, Users, CreditCard, RefreshCw,
  UserPlus, Link as LinkIcon, Trophy, Crown, Clock,
  AlertCircle, CheckCircle2, XCircle, ShieldCheck,
} from 'lucide-react'
import { api, type ReferralStats, type ReferralInvitee } from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { BlurFade } from '@/components/ui/blur-fade'
import { Skeleton } from '@/components/ui/skeleton'
import { Separator } from '@/components/ui/separator'

const LS_STATS    = 'bp_referral_stats_v2'
const LS_INVITEES = 'bp_referral_invitees_v2'
const MILESTONE_50  = 50
const MILESTONE_100 = 100

function makeCode(): string {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
  return Array.from({ length: 8 }, () => chars[Math.floor(Math.random() * chars.length)]).join('')
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' })
}

function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10, m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few
  return many
}

function getOrCreateDemoStats(): ReferralStats {
  const stored = localStorage.getItem(LS_STATS)
  if (stored) return JSON.parse(stored)
  const s: ReferralStats = {
    referral_code: makeCode(),
    total_invited: 0, total_paid: 0, total_valid: 0, total_pending_validation: 0,
    discount_percent: 0, referred_by_email: null, milestone: null,
    milestone_50_progress: 0, milestone_100_progress: 0,
  }
  localStorage.setItem(LS_STATS, JSON.stringify(s))
  return s
}

const STEPS = [
  { n: 1, text: 'Скопируйте реферальную ссылку и поделитесь с коллегами или знакомыми' },
  { n: 2, text: 'Реферал регистрируется по вашей ссылке — код привязывается автоматически' },
  { n: 3, text: 'Реферал оплачивает подписку — вы получаете уведомление' },
  { n: 4, text: 'После 30 дней активности аккаунта реферал «засчитывается»' },
  { n: 5, text: '50 засчитанных рефералов → год «Профи» бесплатно, 100 → «Профи» навсегда' },
]

export default function ReferralsPage() {
  const [stats,    setStats]    = useState<ReferralStats | null>(null)
  const [invitees, setInvitees] = useState<ReferralInvitee[]>([])
  const [loading,  setLoading]  = useState(true)
  const [copied,   setCopied]   = useState(false)
  const [seeding,  setSeeding]  = useState(false)
  const [baseUrl,  setBaseUrl]  = useState('')

  useEffect(() => {
    setBaseUrl(window.location.origin)
    loadData()
  }, [])

  async function loadData() {
    setLoading(true)
    try {
      const [s, inv] = await Promise.all([api.referrals.stats(), api.referrals.invitees()])
      setStats(s); setInvitees(inv)
    } catch {
      const s = getOrCreateDemoStats()
      setStats(s)
      setInvitees(JSON.parse(localStorage.getItem(LS_INVITEES) ?? '[]'))
    } finally { setLoading(false) }
  }

  async function seedDemo() {
    setSeeding(true)
    const now = Date.now()
    const demo: ReferralInvitee[] = [
      { id: '1', email: 'anna.smirnova@mail.ru', joined_at: new Date(now - 35 * 86400000).toISOString(), has_paid: true, paid_at: new Date(now - 32 * 86400000).toISOString(), is_valid: true, validation_days_left: 0, invalidated: false, invalidation_reason: null },
      { id: '2', email: 'ivan.petrov@gmail.com', joined_at: new Date(now - 20 * 86400000).toISOString(), has_paid: true, paid_at: new Date(now - 18 * 86400000).toISOString(), is_valid: false, validation_days_left: 10, invalidated: false, invalidation_reason: null },
      { id: '3', email: 'marina.kozlova@yandex.ru', joined_at: new Date(now - 5 * 86400000).toISOString(), has_paid: false, paid_at: null, is_valid: false, validation_days_left: 0, invalidated: false, invalidation_reason: null },
      { id: '4', email: 'pavel.morozov@bk.ru', joined_at: new Date(now - 10 * 86400000).toISOString(), has_paid: true, paid_at: new Date(now - 8 * 86400000).toISOString(), is_valid: false, validation_days_left: 0, invalidated: true, invalidation_reason: 'Аккаунт удалён до истечения 30 дней' },
    ]
    const validCount = demo.filter(i => i.is_valid).length
    const s: ReferralStats = {
      ...getOrCreateDemoStats(),
      total_invited: demo.length, total_paid: demo.filter(i => i.has_paid).length,
      total_valid: validCount, total_pending_validation: demo.filter(i => i.has_paid && !i.is_valid && !i.invalidated).length,
      discount_percent: Math.min(100, validCount * 5),
      milestone: validCount >= MILESTONE_100 ? 'lifetime' : validCount >= MILESTONE_50 ? 'yearly' : null,
      milestone_50_progress: Math.min(validCount, MILESTONE_50), milestone_100_progress: Math.min(validCount, MILESTONE_100),
    }
    localStorage.setItem(LS_STATS, JSON.stringify(s))
    localStorage.setItem(LS_INVITEES, JSON.stringify(demo))
    setStats(s); setInvitees(demo); setSeeding(false)
  }

  function copyLink() {
    if (!stats || !baseUrl) return
    const link = `${baseUrl}/register?ref=${stats.referral_code}`
    navigator.clipboard.writeText(link).catch(() => {
      const ta = document.createElement('textarea'); ta.value = link; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta)
    })
    setCopied(true); setTimeout(() => setCopied(false), 2200)
  }

  if (loading) return (
    <div className="min-h-screen p-6" style={{ background: '#F6F9FC' }}>
      <div className="max-w-3xl mx-auto space-y-4">
        {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-32 rounded-xl" />)}
      </div>
    </div>
  )

  const referralLink = stats ? `${baseUrl}/register?ref=${stats.referral_code}` : ''
  const valid  = stats?.total_valid ?? 0
  const pct50  = Math.round((Math.min(valid, MILESTONE_50)  / MILESTONE_50)  * 100)
  const pct100 = Math.round((Math.min(valid, MILESTONE_100) / MILESTONE_100) * 100)

  return (
    <div className="min-h-screen" style={{ background: '#F6F9FC' }}>
      <div className="max-w-3xl mx-auto px-4 sm:px-6 py-10 space-y-5">

        {/* Header */}
        <BlurFade inView>
          <div className="flex items-center justify-between flex-wrap gap-3">
            <div className="flex items-center gap-3">
              <div className="w-11 h-11 rounded-2xl flex items-center justify-center shadow-stripe" style={{ background: '#1A73E8' }}>
                <Gift size={20} style={{ color: 'white' }} />
              </div>
              <div>
                <h1 className="text-xl font-bold" style={{ color: '#0A2540', letterSpacing: '-0.02em' }}>Реферальная программа</h1>
                <p className="text-xs text-muted-foreground">50 рефералов → год «Профи» · 100 рефералов → «Профи» навсегда</p>
              </div>
            </div>
            <Button variant="ghost" size="sm" onClick={seedDemo} disabled={seeding} className="flex items-center gap-1.5 text-xs">
              <RefreshCw size={12} className={seeding ? 'animate-spin' : ''} /> Демо-данные
            </Button>
          </div>
        </BlurFade>

        {/* Milestone banner */}
        {stats?.milestone && (
          <BlurFade inView delay={0.03}>
            <Card className="border-2 shadow-stripe" style={{ borderColor: 'rgba(26,115,232,0.3)', background: 'rgba(26,115,232,0.04)' }}>
              <CardContent className="p-4 flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: 'rgba(26,115,232,0.12)' }}>
                  {stats.milestone === 'lifetime' ? <Crown size={18} style={{ color: '#1A73E8' }} /> : <Trophy size={18} style={{ color: '#1A73E8' }} />}
                </div>
                <div>
                  <p className="font-semibold text-sm" style={{ color: '#1A73E8' }}>
                    {stats.milestone === 'lifetime' ? 'Пожизненная подписка «Профи» активна!' : 'Годовая подписка «Профи» активна!'}
                  </p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {stats.milestone === 'lifetime' ? 'Вы привлекли 100+ оплативших рефералов.' : 'Вы привлекли 50+ оплативших рефералов.'}
                  </p>
                </div>
              </CardContent>
            </Card>
          </BlurFade>
        )}

        {/* Stats row */}
        {stats && (
          <BlurFade inView delay={0.05}>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {[
                { icon: <UserPlus size={14} />,    label: 'Приглашено',  value: stats.total_invited, color: '#425466' },
                { icon: <CreditCard size={14} />,  label: 'Оплатили',    value: stats.total_paid,    color: '#425466' },
                { icon: <CheckCircle2 size={14} />, label: 'Засчитаны',  value: stats.total_valid,   color: '#1A73E8' },
                { icon: <Clock size={14} />,        label: 'Ожидают',    value: stats.total_pending_validation, color: '#d97706' },
              ].map(({ icon, label, value, color }) => (
                <Card key={label} className="shadow-stripe border-border/60">
                  <CardContent className="p-4">
                    <div className="flex items-center gap-1.5 mb-2 text-muted-foreground">{icon}<span className="text-xs">{label}</span></div>
                    <p className="text-2xl font-bold" style={{ color }}>{value}</p>
                  </CardContent>
                </Card>
              ))}
            </div>
          </BlurFade>
        )}

        {/* Referral link card */}
        {stats && (
          <BlurFade inView delay={0.08}>
            <Card className="shadow-stripe">
              <CardHeader className="pb-3">
                <CardTitle className="text-base" style={{ color: '#0A2540' }}>Ваша реферальная ссылка</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex items-center justify-center py-4 rounded-xl" style={{ background: 'rgba(26,115,232,0.05)', border: '1.5px dashed rgba(26,115,232,0.3)' }}>
                  <span className="font-mono font-bold text-3xl" style={{ color: '#1A73E8', letterSpacing: '0.35em' }}>
                    {stats.referral_code}
                  </span>
                </div>
                <div className="flex gap-2">
                  <Input readOnly value={referralLink} className="font-mono text-xs text-muted-foreground" />
                  <Button onClick={copyLink} className="shrink-0 flex items-center gap-1.5">
                    {copied ? <><Check size={14} />Скопировано</> : <><Copy size={14} />Копировать</>}
                  </Button>
                </div>
              </CardContent>
            </Card>
          </BlurFade>
        )}

        {/* Milestone progress */}
        {stats && (
          <BlurFade inView delay={0.1}>
            <Card className="shadow-stripe">
              <CardHeader className="pb-4">
                <CardTitle className="text-base" style={{ color: '#0A2540' }}>Прогресс к наградам</CardTitle>
              </CardHeader>
              <CardContent className="space-y-6">
                {[
                  { icon: <Trophy size={15} />, label: 'Годовая подписка «Профи»', sublabel: '50 рефералов', progress: pct50, current: Math.min(valid, MILESTONE_50), target: MILESTONE_50, reached: valid >= MILESTONE_50, reward: '1 год «Профи» бесплатно' },
                  { icon: <Crown size={15} />,  label: 'Пожизненная «Профи»',      sublabel: '100 рефералов', progress: pct100, current: Math.min(valid, MILESTONE_100), target: MILESTONE_100, reached: valid >= MILESTONE_100, reward: 'Навсегда «Профи» бесплатно' },
                ].map(({ icon, label, sublabel, progress, current, target, reached, reward }, idx) => (
                  <div key={idx}>
                    <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
                      <div className="flex items-center gap-2 text-sm font-medium" style={{ color: '#0A2540' }}>
                        <span style={{ color: '#1A73E8' }}>{icon}</span> {label}
                        <span className="text-xs font-normal text-muted-foreground">{sublabel}</span>
                      </div>
                      {reached
                        ? <Badge variant="default" className="text-xs flex items-center gap-1"><Check size={10} />Получено</Badge>
                        : <span className="text-xs text-muted-foreground">{reward}</span>
                      }
                    </div>
                    <Progress value={progress} className="h-2 mb-1" />
                    <div className="flex justify-between text-xs text-muted-foreground">
                      <span>{current} / {target} {plural(current, 'реферал', 'реферала', 'рефералов')}</span>
                      <span>{progress}%</span>
                    </div>
                    {idx === 0 && <Separator className="mt-5" />}
                  </div>
                ))}
              </CardContent>
            </Card>
          </BlurFade>
        )}

        {/* Referred by */}
        {stats?.referred_by_email && (
          <BlurFade inView delay={0.12}>
            <Card className="shadow-stripe">
              <CardContent className="p-4 flex items-center gap-2">
                <Users size={14} className="text-muted-foreground" />
                <span className="text-sm text-muted-foreground">Вас пригласил: <strong style={{ color: '#0A2540' }}>{stats.referred_by_email}</strong></span>
              </CardContent>
            </Card>
          </BlurFade>
        )}

        {/* How it works */}
        <BlurFade inView delay={0.14}>
          <Card className="shadow-stripe">
            <CardHeader className="pb-3"><CardTitle className="text-base" style={{ color: '#0A2540' }}>Как это работает</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              {STEPS.map(({ n, text }) => (
                <div key={n} className="flex items-start gap-3">
                  <div className="w-6 h-6 rounded-full flex items-center justify-center shrink-0 text-xs font-bold mt-0.5" style={{ background: 'rgba(26,115,232,0.1)', color: '#1A73E8' }}>{n}</div>
                  <p className="text-sm text-muted-foreground leading-relaxed">{text}</p>
                </div>
              ))}
            </CardContent>
          </Card>
        </BlurFade>

        {/* Anti-fraud rules */}
        <BlurFade inView delay={0.16}>
          <Card className="shadow-stripe">
            <CardHeader className="pb-3">
              <div className="flex items-center gap-2">
                <ShieldCheck size={15} style={{ color: '#1A73E8' }} />
                <CardTitle className="text-base" style={{ color: '#0A2540' }}>Условия зачёта реферала</CardTitle>
              </div>
            </CardHeader>
            <CardContent>
              <ul className="space-y-2">
                {[
                  'Реферал должен оплатить минимум 1 месяц подписки',
                  'Аккаунт реферала должен просуществовать не менее 30 дней',
                  'Если реферал удалит аккаунт раньше 30 дней — засчитанный бонус аннулируется',
                  'С одного IP можно зарегистрировать не более 3 аккаунтов за 24 часа',
                ].map((text, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-muted-foreground">
                    <span className="w-1.5 h-1.5 rounded-full mt-2 shrink-0" style={{ background: '#1A73E8' }} />
                    {text}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </BlurFade>

        {/* Invitees */}
        <BlurFade inView delay={0.18}>
          <Card className="shadow-stripe overflow-hidden">
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base" style={{ color: '#0A2540' }}>Приглашённые пользователи</CardTitle>
                {invitees.length > 0 && <Badge variant="secondary">{invitees.length}</Badge>}
              </div>
            </CardHeader>
            <CardContent className="p-0">
              {invitees.length === 0 ? (
                <div className="flex flex-col items-center py-12 text-center px-6">
                  <UserPlus size={28} className="mb-3 text-muted-foreground opacity-40" />
                  <p className="font-medium text-sm mb-1" style={{ color: '#0A2540' }}>Вы пока никого не пригласили</p>
                  <p className="text-xs text-muted-foreground">Поделитесь ссылкой — за 50 рефералов вы получите год «Профи» бесплатно</p>
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Email</TableHead>
                      <TableHead>Регистрация</TableHead>
                      <TableHead className="text-right">Статус</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {invitees.map(inv => (
                      <TableRow key={inv.id} style={{ opacity: inv.invalidated ? 0.55 : 1 }}>
                        <TableCell>
                          <p className="text-sm font-medium" style={{ color: '#0A2540' }}>{inv.email}</p>
                          {inv.invalidated && inv.invalidation_reason && (
                            <p className="text-xs flex items-center gap-1 mt-0.5" style={{ color: '#dc2626' }}><AlertCircle size={10} />{inv.invalidation_reason}</p>
                          )}
                          {!inv.invalidated && inv.has_paid && !inv.is_valid && inv.validation_days_left > 0 && (
                            <p className="text-xs text-muted-foreground flex items-center gap-1 mt-0.5"><Clock size={10} />ещё {inv.validation_days_left} {plural(inv.validation_days_left, 'день', 'дня', 'дней')}</p>
                          )}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground whitespace-nowrap">{fmtDate(inv.joined_at)}</TableCell>
                        <TableCell className="text-right">
                          <InviteeStatusBadge inv={inv} />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </BlurFade>
      </div>
    </div>
  )
}

function InviteeStatusBadge({ inv }: { inv: ReferralInvitee }) {
  if (inv.invalidated)   return <Badge variant="destructive" className="text-xs"><XCircle size={9} className="mr-1" />Аннулирован</Badge>
  if (inv.is_valid)      return <Badge variant="default" className="text-xs"><CheckCircle2 size={9} className="mr-1" />Засчитан</Badge>
  if (inv.has_paid)      return <Badge variant="warning" className="text-xs"><Clock size={9} className="mr-1" />Ожидает</Badge>
  return <Badge variant="secondary" className="text-xs">Не оплатил</Badge>
}
