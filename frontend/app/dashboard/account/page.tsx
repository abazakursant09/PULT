'use client'
import { useEffect, useState, useCallback } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import {
  User, Shield, ShieldCheck, ShieldOff, Bell, Globe, AlertTriangle,
  Send, Check, RefreshCw, Copy, Calendar, Clock, Trash2, AlertCircle,
} from 'lucide-react'
import { api, type TelegramSettings } from '@/lib/api'
import { clearSession } from '@/lib/session'
import { LanguageSwitcher } from '@/components/LanguageSwitcher'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import Link from 'next/link'

const DAYS = [
  { value: 'monday',    label: 'Пн' },
  { value: 'tuesday',   label: 'Вт' },
  { value: 'wednesday', label: 'Ср' },
  { value: 'thursday',  label: 'Чт' },
  { value: 'friday',    label: 'Пт' },
  { value: 'saturday',  label: 'Сб' },
  { value: 'sunday',    label: 'Вс' },
]

const EVENT_TYPES: { key: keyof TelegramSettings; label: string; desc: string; critical?: boolean }[] = [
  { key: 'notify_bad_review',      label: 'Проблемный отзыв',              desc: 'Отзыв с юридическим риском',           critical: true },
  { key: 'notify_offer_change',    label: 'Изменение оферты маркетплейса', desc: 'Новые условия от WB, Ozon, ЯМ',        critical: true },
  { key: 'notify_price_drop',      label: 'Резкое падение цены конкурента',desc: 'Конкурент снизил цену > 10%',          critical: true },
  { key: 'notify_negative_review', label: 'Новый негативный отзыв',        desc: 'Отзыв с оценкой 1–2 звезды' },
  { key: 'notify_trial_end',       label: 'Окончание пробного периода',    desc: 'За 3 дня до конца бесплатного доступа' },
]

const INTELLIGENCE_TYPES: { key: keyof TelegramSettings; label: string; desc: string; badge?: string }[] = [
  { key: 'notify_seo_opportunity', label: 'SEO-возможности',        desc: 'Карточка снижает CTR — 1 клик до авто-пересборки', badge: 'авто' },
  { key: 'notify_sales_growth',    label: 'Рост продаж и рейтинга', desc: 'Товар растёт — масштабируйте вовремя' },
  { key: 'notify_insights',        label: 'Критические алерты',     desc: 'Кризис маржи, высокий ДРР, конец остатков',         badge: 'важно' },
  { key: 'notify_weekly_report',   label: 'Weekly Intelligence',    desc: 'Итог недели: rebuilds, CTR, лучший стиль, потенциал' },
  { key: 'notify_ab_results',      label: 'Победитель стиля',       desc: 'Уведомление когда стиль показал CTR >7% vs контроль' },
  { key: 'notify_retention',       label: 'Напоминание вернуться',  desc: 'Если вы не заходили N дней — Пульт напомнит' },
]

type Tab = 'profile' | 'security' | 'notifications' | 'language' | 'danger'

const TABS: { id: Tab; label: string; icon: React.ElementType }[] = [
  { id: 'profile',       label: 'Профиль',      icon: User          },
  { id: 'security',      label: 'Безопасность', icon: Shield        },
  { id: 'notifications', label: 'Уведомления',  icon: Bell          },
  { id: 'language',      label: 'Язык',         icon: Globe         },
  { id: 'danger',        label: 'Опасная зона', icon: AlertTriangle },
]

// ─── Shared Card ──────────────────────────────────────────────────────────────
function DarkCard({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`rounded-[8px] p-6 ${className}`} style={{ background: 'var(--bg)', border: '1px solid var(--surface)' }}>
      {children}
    </div>
  )
}

function SectionIcon({ icon: Icon, gold }: { icon: React.ElementType; gold?: boolean }) {
  return (
    <div className="w-9 h-9 rounded-[8px] flex items-center justify-center shrink-0"
         style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(124,58,237,0.18)' }}>
      <Icon size={16} style={{ color: 'var(--violet-text)' }} />
    </div>
  )
}

// ─── Profile Tab ──────────────────────────────────────────────────────────────
function ProfileTab() {
  const [user, setUser] = useState<{ name: string; email: string; plan?: string } | null>(null)

  useEffect(() => {
    const s = localStorage.getItem('user')
    if (s) try { setUser(JSON.parse(s)) } catch {}
  }, [])

  const planLabel: Record<string, string> = {
    master: 'Мастер', profi: 'Профи', maximum: 'Максимальный', free: 'Бесплатный',
  }

  return (
    <div className="space-y-4">
      <DarkCard>
        <div className="flex items-center gap-4 mb-6">
          <div
            className="w-14 h-14 rounded-[8px] flex items-center justify-center text-[22px] font-bold shrink-0"
            style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(124,58,237,0.18)', color: 'var(--violet-text)' }}
          >
            {user?.name.charAt(0).toUpperCase() ?? '?'}
          </div>
          <div>
            <p className="text-[18px] font-bold" style={{ color: 'var(--text)' }}>{user?.name ?? '—'}</p>
            <p className="text-[13px] mt-0.5" style={{ color: 'var(--text-2)' }}>{user?.email ?? '—'}</p>
            {user?.plan && (
              <span className="badge badge-gold mt-2 inline-block">
                {planLabel[user.plan] ?? user.plan}
              </span>
            )}
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {[
            { label: 'ИМЯ',    value: user?.name ?? '—' },
            { label: 'EMAIL',  value: user?.email ?? '—' },
            { label: 'ТАРИФ',  value: planLabel[user?.plan ?? ''] ?? user?.plan ?? '—' },
            { label: 'СТАТУС', value: 'Активен' },
          ].map(({ label, value }) => (
            <div key={label} className="p-4 rounded-[8px]" style={{ background: 'var(--bg)', border: '1px solid var(--surface)' }}>
              <p className="label mb-1">{label}</p>
              <p className="text-[14px] font-medium" style={{ color: 'var(--text)' }}>{value}</p>
            </div>
          ))}
        </div>

        <p className="mt-5 text-[13px]" style={{ color: 'var(--text-3)' }}>
          Для изменения имени или email обратитесь в{' '}
          <Link href="/support" style={{ color: 'var(--violet-text)', textDecoration: 'none' }}>службу поддержки</Link>.
        </p>
      </DarkCard>
    </div>
  )
}

// ─── Security Tab ─────────────────────────────────────────────────────────────
function SecurityTab() {
  type Phase = 'idle' | 'setup' | 'verify' | 'disable'
  const [mfaEnabled, setMfaEnabled] = useState(false)
  const [loading,    setLoading]    = useState(true)
  const [phase,      setPhase]      = useState<Phase>('idle')
  const [secret,     setSecret]     = useState('')
  const [otpauth,    setOtpauth]    = useState('')
  const [code,       setCode]       = useState('')
  const [working,    setWorking]    = useState(false)
  const [error,      setError]      = useState('')
  const [success,    setSuccess]    = useState('')
  const [copied,     setCopied]     = useState(false)

  useEffect(() => {
    api.mfa.status()
      .then(s => setMfaEnabled(s.enabled))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  async function startSetup() {
    setError(''); setWorking(true)
    try {
      const data = await api.mfa.setup()
      setSecret(data.secret); setOtpauth(data.otpauth); setPhase('setup')
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Ошибка') }
    finally { setWorking(false) }
  }

  async function verifyCode() {
    if (code.length !== 6) return
    setError(''); setWorking(true)
    try {
      await api.mfa.verify(code)
      setMfaEnabled(true); setPhase('idle'); setCode('')
      setSuccess('MFA включена — аккаунт защищён')
      setTimeout(() => setSuccess(''), 5000)
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Неверный код') }
    finally { setWorking(false) }
  }

  async function disableMfa() {
    if (code.length !== 6) return
    setError(''); setWorking(true)
    try {
      await api.mfa.disable(code)
      setMfaEnabled(false); setPhase('idle'); setCode('')
      setSuccess('MFA отключена')
      setTimeout(() => setSuccess(''), 4000)
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Неверный код') }
    finally { setWorking(false) }
  }

  function copySecret() {
    navigator.clipboard.writeText(secret).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000) })
  }

  if (loading) return (
    <div className="flex justify-center py-12">
      <div className="spinner" />
    </div>
  )

  return (
    <div className="space-y-4">
      {success && (
        <div className="px-4 py-3 rounded-[8px] flex items-center gap-3 text-[13px]"
             style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(124,58,237,0.22)', color: 'var(--violet-text)' }}>
          <Check size={14} /> {success}
        </div>
      )}
      {error && (
        <div className="px-4 py-3 rounded-[8px] flex items-center gap-3 text-[13px]"
             style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', color: 'var(--danger)' }}>
          <AlertTriangle size={14} /> {error}
        </div>
      )}

      <DarkCard>
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-5">
          <div className="flex items-center gap-4">
            <div className="w-10 h-10 rounded-[8px] flex items-center justify-center shrink-0"
                 style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(124,58,237,0.18)' }}>
              {mfaEnabled
                ? <ShieldCheck size={18} style={{ color: 'var(--violet-text)' }} />
                : <Shield size={18} style={{ color: 'var(--violet-text)' }} />
              }
            </div>
            <div>
              <p className="text-[15px] font-semibold" style={{ color: 'var(--text)' }}>Двухфакторная аутентификация (2FA)</p>
              <p className="text-[13px] mt-0.5" style={{ color: 'var(--text-2)' }}>
                {mfaEnabled ? 'Аккаунт защищён TOTP-кодом' : 'Защита не активирована'}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            <span className="badge" style={mfaEnabled
              ? { background: 'rgba(124,58,237,0.10)', color: 'var(--violet-text)', borderColor: 'rgba(124,58,237,0.25)' }
              : { background: 'rgba(255,255,255,0.04)', color: 'var(--text-2)', borderColor: 'var(--surface)' }
            }>
              {mfaEnabled ? '✓ Включено' : 'Выключено'}
            </span>
            {phase === 'idle' && (
              mfaEnabled ? (
                <Button variant="ghost" size="sm" onClick={() => { setPhase('disable'); setCode(''); setError('') }}>
                  <ShieldOff size={13} /> Отключить
                </Button>
              ) : (
                <Button loading={working} onClick={startSetup}>
                  {!working && <><Shield size={13} /> Включить 2FA</>}
                </Button>
              )
            )}
          </div>
        </div>

        {phase === 'setup' && (
          <div className="mt-6 space-y-4 pt-6" style={{ borderTop: '1px solid var(--surface)' }}>
            <p className="text-[13px] font-medium" style={{ color: 'var(--text)' }}>1. Отсканируйте QR-код в Google Authenticator или Aegis</p>
            <div className="p-3 rounded-[8px] inline-block" style={{ background: 'var(--text)', border: '1px solid var(--surface)' }}>
              <img
                src={`https://api.qrserver.com/v1/create-qr-code/?data=${encodeURIComponent(otpauth)}&size=180x180&margin=4`}
                alt="QR-код 2FA" width={180} height={180} style={{ borderRadius: 4, display: 'block' }}
              />
            </div>
            <div>
              <p className="text-[13px] mb-2" style={{ color: 'var(--text-2)' }}>Или введите ключ вручную:</p>
              <div className="flex items-center gap-2">
                <code
                  className="flex-1 px-3 py-2 rounded-[8px] text-[13px] mono"
                  style={{ background: 'var(--bg)', border: '1px solid var(--surface)', color: 'var(--violet-text)', letterSpacing: '0.08em', wordBreak: 'break-all' }}
                >
                  {secret}
                </code>
                <button
                  onClick={copySecret}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-3)', padding: '4px 8px' }}
                  onMouseEnter={e => { e.currentTarget.style.color = 'var(--violet-text)' }}
                  onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-3)' }}
                >
                  {copied ? <Check size={14} style={{ color: 'var(--violet-text)' }} /> : <Copy size={14} />}
                </button>
              </div>
            </div>
            <div>
              <p className="text-[13px] font-medium mb-2" style={{ color: 'var(--text)' }}>2. Введите 6-значный код из приложения</p>
              <div className="flex gap-3">
                <Input
                  type="text" inputMode="numeric" pattern="[0-9]{6}" maxLength={6}
                  value={code} onChange={e => setCode(e.target.value.replace(/\D/g, ''))}
                  placeholder="000000" className="font-mono text-center"
                  style={{ fontSize: '1.25rem', letterSpacing: '0.3em', maxWidth: 160 }} autoFocus
                />
                <Button onClick={verifyCode} disabled={working || code.length !== 6} loading={working}>
                  {!working && <><Check size={14} /> Подтвердить</>}
                </Button>
              </div>
            </div>
            <Button variant="ghost" size="sm" onClick={() => { setPhase('idle'); setCode(''); setError('') }}>Отмена</Button>
          </div>
        )}

        {phase === 'disable' && (
          <div className="mt-6 pt-6 space-y-4" style={{ borderTop: '1px solid var(--surface)' }}>
            <div className="px-4 py-3 rounded-[8px] flex items-center gap-3 text-[13px]"
                 style={{ background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.15)', color: 'var(--text-2)' }}>
              <AlertTriangle size={14} style={{ color: 'var(--danger)' }} />
              Отключение 2FA снизит защиту аккаунта
            </div>
            <div>
              <p className="text-[13px] font-medium mb-2" style={{ color: 'var(--text)' }}>Введите код из приложения для подтверждения</p>
              <div className="flex gap-3">
                <Input
                  type="text" inputMode="numeric" pattern="[0-9]{6}" maxLength={6}
                  value={code} onChange={e => setCode(e.target.value.replace(/\D/g, ''))}
                  placeholder="000000" className="font-mono text-center"
                  style={{ fontSize: '1.25rem', letterSpacing: '0.3em', maxWidth: 160 }} autoFocus
                />
                <Button variant="destructive" onClick={disableMfa} disabled={working || code.length !== 6} loading={working}>
                  {!working && 'Отключить'}
                </Button>
              </div>
            </div>
            <Button variant="ghost" size="sm" onClick={() => { setPhase('idle'); setCode(''); setError('') }}>Отмена</Button>
          </div>
        )}
      </DarkCard>
    </div>
  )
}

// ─── Notifications Tab ────────────────────────────────────────────────────────
function NotificationsTab() {
  const [chatId,        setChatId]        = useState('')
  const [savedChatId,   setSavedChatId]   = useState<string | null>(null)
  const [chatSaving,    setChatSaving]    = useState(false)
  const [chatSaved,     setChatSaved]     = useState(false)
  const [chatError,     setChatError]     = useState('')
  const [testing,       setTesting]       = useState(false)
  const [testResult,    setTestResult]    = useState<'ok' | 'err' | null>(null)
  const [triggering,    setTriggering]    = useState(false)
  const [triggerResult, setTriggerResult] = useState<number | null>(null)
  const [settings,    setSettings]    = useState<TelegramSettings | null>(null)
  const [sLoad,       setSLoad]       = useState(true)
  const [saving,      setSaving]      = useState(false)
  const [saved,       setSaved]       = useState(false)

  useEffect(() => {
    Promise.all([api.telegram.getChatId(), api.telegram.getSettings()])
      .then(([cid, s]) => { setSavedChatId(cid.telegram_chat_id); setChatId(cid.telegram_chat_id ?? ''); setSettings(s) })
      .catch(() => {})
      .finally(() => setSLoad(false))
  }, [])

  const setField = useCallback(<K extends keyof TelegramSettings>(key: K, val: TelegramSettings[K]) => {
    setSettings(s => s ? { ...s, [key]: val } : s)
  }, [])

  async function saveChatId() {
    setChatSaving(true); setChatError(''); setChatSaved(false)
    try {
      const res = await api.telegram.updateChatId(chatId.trim() || null)
      setSavedChatId(res.telegram_chat_id); setChatSaved(true)
      setTimeout(() => setChatSaved(false), 2500)
    } catch (e) { setChatError(e instanceof Error ? e.message : 'Ошибка') }
    finally { setChatSaving(false) }
  }

  async function sendTest() {
    setTesting(true); setTestResult(null)
    try { await api.telegram.test(); setTestResult('ok') }
    catch { setTestResult('err') }
    finally { setTesting(false); setTimeout(() => setTestResult(null), 4000) }
  }

  async function triggerInsights() {
    setTriggering(true); setTriggerResult(null)
    try {
      const res = await api.telegram.triggerInsights()
      setTriggerResult(res.notifications_sent)
    } catch { setTriggerResult(-1) }
    finally { setTriggering(false); setTimeout(() => setTriggerResult(null), 5000) }
  }

  async function saveSettings() {
    if (!settings) return
    setSaving(true); setSaved(false)
    try {
      const u = await api.telegram.updateSettings(settings)
      setSettings(u); setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch {}
    finally { setSaving(false) }
  }

  return (
    <div className="space-y-4">
      <DarkCard>
        <div className="flex items-center gap-3 mb-5">
          <SectionIcon icon={Send} />
          <p className="text-[15px] font-semibold" style={{ color: 'var(--text)' }}>1. Подключить Telegram</p>
        </div>
        <div className="rounded-[8px] p-4 mb-4 text-[13px]" style={{ background: 'var(--bg)', border: '1px solid var(--surface)' }}>
          <p className="font-medium mb-2" style={{ color: 'var(--text)' }}>Как узнать Chat ID:</p>
          <ol className="space-y-1 pl-4 list-decimal" style={{ color: 'var(--text-2)' }}>
            <li>Откройте Telegram → найдите <strong style={{ color: 'var(--violet-text)' }}>@userinfobot</strong></li>
            <li>Нажмите /start — бот пришлёт ваш Chat ID</li>
            <li>Вставьте его ниже и нажмите «Сохранить»</li>
          </ol>
        </div>
        <div className="flex gap-3 flex-wrap">
          <Input
            className="flex-1 min-w-0 font-mono"
            placeholder="123456789"
            value={chatId}
            onChange={e => setChatId(e.target.value)}
          />
          <Button onClick={saveChatId} loading={chatSaving} className="shrink-0">
            {!chatSaving && (chatSaved ? <><Check size={13} /> Сохранено</> : 'Сохранить')}
          </Button>
        </div>
        {chatError && <p className="mt-2 text-[13px]" style={{ color: 'var(--danger)' }}>{chatError}</p>}
        {savedChatId && (
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <span className="text-[13px]" style={{ color: 'var(--text-2)' }}>
              Chat ID: <code style={{ color: 'var(--violet-text)' }}>{savedChatId}</code>
            </span>
            <Button variant="ghost" size="sm" onClick={sendTest} loading={testing}>
              {!testing && (
                testResult === 'ok'  ? <><Check size={11} style={{ color: 'var(--violet-text)' }} /> Отправлено</> :
                testResult === 'err' ? <><AlertTriangle size={11} style={{ color: 'var(--danger)' }} /> Ошибка</> :
                'Тест'
              )}
            </Button>
          </div>
        )}
      </DarkCard>

      <DarkCard>
        <div className="flex items-center gap-3 mb-5">
          <SectionIcon icon={Bell} />
          <p className="text-[15px] font-semibold" style={{ color: 'var(--text)' }}>2. Типы уведомлений</p>
        </div>
        {sLoad || !settings ? (
          <div className="flex justify-center py-8"><div className="spinner" /></div>
        ) : (
          <div className="space-y-2">
            {EVENT_TYPES.map(({ key, label, desc, critical }) => (
              <div
                key={key}
                className="flex items-center justify-between gap-4 px-4 py-3 rounded-[8px]"
                style={{ background: 'var(--bg)', border: '1px solid var(--surface)' }}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-[13px] font-medium" style={{ color: 'var(--text)' }}>{label}</span>
                    {critical && <span className="badge badge-danger">критично</span>}
                  </div>
                  <p className="text-[12px] mt-0.5" style={{ color: 'var(--text-3)' }}>{desc}</p>
                </div>
                <Switch
                  checked={settings[key] as boolean}
                  onCheckedChange={v => setField(key, v as TelegramSettings[typeof key])}
                />
              </div>
            ))}
          </div>
        )}
      </DarkCard>

      {settings && (
        <DarkCard>
          <div className="flex items-center gap-3 mb-5">
            <SectionIcon icon={RefreshCw} />
            <div>
              <p className="text-[15px] font-semibold" style={{ color: 'var(--text)' }}>3. Intelligence Loop</p>
              <p className="text-[12px] mt-0.5" style={{ color: 'var(--text-3)' }}>Пульт сам находит инсайты и пишет в Telegram каждые 30 минут</p>
            </div>
          </div>
          <div className="space-y-2 mb-4">
            {INTELLIGENCE_TYPES.map(({ key, label, desc, badge }) => (
              <div
                key={key}
                className="flex items-center justify-between gap-4 px-4 py-3 rounded-[8px]"
                style={{ background: 'var(--bg)', border: '1px solid var(--surface)' }}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-[13px] font-medium" style={{ color: 'var(--text)' }}>{label}</span>
                    {badge === 'авто' && (
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold"
                            style={{ background: 'rgba(110,106,252,0.15)', color: 'var(--violet)' }}>авто</span>
                    )}
                    {badge === 'важно' && <span className="badge badge-danger">важно</span>}
                  </div>
                  <p className="text-[12px] mt-0.5" style={{ color: 'var(--text-3)' }}>{desc}</p>
                </div>
                <Switch
                  checked={settings[key] as boolean}
                  onCheckedChange={v => setField(key, v as TelegramSettings[typeof key])}
                />
              </div>
            ))}
          </div>

          {settings.notify_retention && (
            <div className="flex items-center gap-3 px-4 py-3 rounded-[8px] mb-4"
                 style={{ background: 'var(--bg)', border: '1px solid var(--surface)' }}>
              <span className="text-[13px]" style={{ color: 'var(--text-2)' }}>Напоминать через</span>
              <select
                value={settings.retention_inactive_days}
                onChange={e => setField('retention_inactive_days', Number(e.target.value))}
                className="h-8 rounded-[6px] px-2 text-[13px] font-medium"
                style={{ background: 'var(--surface)', border: '1px solid var(--surface-h)', color: 'var(--text)' }}
              >
                {[1,2,3,5,7,14].map(d => <option key={d} value={d}>{d} {d === 1 ? 'день' : d < 5 ? 'дня' : 'дней'}</option>)}
              </select>
              <span className="text-[13px]" style={{ color: 'var(--text-2)' }}>бездействия</span>
            </div>
          )}

          {savedChatId && (
            <div className="flex items-center gap-3">
              <Button variant="ghost" size="sm" onClick={triggerInsights} loading={triggering}>
                {!triggering && (
                  triggerResult === null          ? <><RefreshCw size={11} /> Проверить сейчас</> :
                  triggerResult === -1            ? <><AlertTriangle size={11} style={{ color: 'var(--danger)' }} /> Ошибка</> :
                  triggerResult === 0             ? <><Check size={11} style={{ color: 'var(--text-2)' }} /> Новых нет</> :
                                                    <><Check size={11} style={{ color: 'var(--violet)' }} /> Отправлено {triggerResult}</>
                )}
              </Button>
              <span className="text-[11px]" style={{ color: 'var(--line)' }}>Принудительный запуск Intelligence Loop</span>
            </div>
          )}
        </DarkCard>
      )}

      {settings && (
        <DarkCard>
          <div className="flex items-center gap-3 mb-5">
            <SectionIcon icon={Calendar} />
            <p className="text-[15px] font-semibold" style={{ color: 'var(--text)' }}>4. Плановые отчёты</p>
          </div>

          <div className="space-y-3">
            <div className="rounded-[8px] p-4" style={{ background: 'var(--bg)', border: '1px solid var(--surface)' }}>
              <div className="flex items-center justify-between mb-2">
                <div>
                  <p className="text-[13px] font-medium" style={{ color: 'var(--text)' }}>Ежедневный отчёт</p>
                  <p className="text-[12px] mt-0.5" style={{ color: 'var(--text-3)' }}>Сводка по товарам, отзывам и ценам</p>
                </div>
                <Switch checked={settings.daily_report} onCheckedChange={v => setField('daily_report', v)} />
              </div>
              {settings.daily_report && (
                <div className="flex items-center gap-2 mt-3">
                  <Clock size={13} style={{ color: 'var(--text-3)' }} />
                  <span className="text-[12px]" style={{ color: 'var(--text-2)' }}>Время:</span>
                  <Input
                    type="time"
                    value={settings.daily_report_time}
                    onChange={e => setField('daily_report_time', e.target.value)}
                    className="h-8 text-sm"
                    style={{ width: 110 }}
                  />
                </div>
              )}
            </div>

            <div className="rounded-[8px] p-4" style={{ background: 'var(--bg)', border: '1px solid var(--surface)' }}>
              <div className="flex items-center justify-between mb-2">
                <div>
                  <p className="text-[13px] font-medium" style={{ color: 'var(--text)' }}>Еженедельная сводка</p>
                  <p className="text-[12px] mt-0.5" style={{ color: 'var(--text-3)' }}>Детальный отчёт: динамика, топ товары</p>
                </div>
                <Switch checked={settings.weekly_summary} onCheckedChange={v => setField('weekly_summary', v)} />
              </div>
              {settings.weekly_summary && (
                <div className="space-y-2 mt-3">
                  <p className="text-[12px]" style={{ color: 'var(--text-2)' }}>День недели:</p>
                  <div className="flex flex-wrap gap-1.5">
                    {DAYS.map(d => {
                      const isActive = settings.weekly_summary_day === d.value
                      return (
                        <button
                          key={d.value}
                          type="button"
                          onClick={() => setField('weekly_summary_day', d.value)}
                          className="w-9 h-9 rounded-[8px] text-[12px] font-semibold transition-all duration-200"
                          style={{
                            background: isActive ? 'var(--violet-text)' : 'var(--bg)',
                            color: isActive ? 'var(--bg)' : 'var(--text-2)',
                            border: `1px solid ${isActive ? 'var(--violet-text)' : 'var(--surface)'}`,
                            cursor: 'pointer',
                          }}
                        >
                          {d.label}
                        </button>
                      )
                    })}
                  </div>
                  <div className="flex items-center gap-2 mt-1">
                    <Clock size={13} style={{ color: 'var(--text-3)' }} />
                    <span className="text-[12px]" style={{ color: 'var(--text-2)' }}>Время:</span>
                    <Input
                      type="time"
                      value={settings.weekly_summary_time}
                      onChange={e => setField('weekly_summary_time', e.target.value)}
                      className="h-8 text-sm"
                      style={{ width: 110 }}
                    />
                  </div>
                </div>
              )}
            </div>
          </div>

          <Button onClick={saveSettings} loading={saving} className="mt-5">
            {!saving && (saved ? <><Check size={14} /> Сохранено</> : 'Сохранить настройки')}
          </Button>
        </DarkCard>
      )}
    </div>
  )
}

// ─── Language Tab ─────────────────────────────────────────────────────────────
function LanguageTab() {
  return (
    <DarkCard>
      <div className="flex items-center gap-3 mb-5">
        <SectionIcon icon={Globe} />
        <p className="text-[15px] font-semibold" style={{ color: 'var(--text)' }}>Язык интерфейса</p>
      </div>
      <p className="text-[13px] mb-5" style={{ color: 'var(--text-2)' }}>
        Доступны три языка: русский, английский и китайский.
        Выбранный язык сохраняется в браузере.
      </p>
      <div className="flex items-center gap-3">
        <span className="text-[13px] font-medium" style={{ color: 'var(--text)' }}>Текущий язык:</span>
        <LanguageSwitcher />
      </div>
    </DarkCard>
  )
}

// ─── Danger Tab ───────────────────────────────────────────────────────────────
function DangerTab() {
  const router = useRouter()
  const [confirm,  setConfirm]  = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error,    setError]    = useState('')

  async function deleteAccount() {
    setDeleting(true); setError('')
    try {
      await api.account.delete()
      clearSession()
      router.push('/login')
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Ошибка')
      setDeleting(false); setConfirm(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-[8px] p-6 overflow-hidden" style={{ background: 'var(--bg)', border: '1px solid rgba(239,68,68,0.25)' }}>
        <div className="flex items-center gap-3 mb-5">
          <div className="w-9 h-9 rounded-[8px] flex items-center justify-center shrink-0"
               style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)' }}>
            <AlertCircle size={16} style={{ color: 'var(--danger)' }} />
          </div>
          <p className="text-[15px] font-semibold" style={{ color: 'var(--danger)' }}>Удаление аккаунта</p>
        </div>
        <p className="text-[13px] mb-5" style={{ color: 'var(--text-2)', lineHeight: 1.65 }}>
          После удаления все ваши данные будут деактивированы. Аккаунт можно восстановить,
          зарегистрировавшись с тем же email в течение 30 дней. Реферальная история сохраняется.
        </p>
        {error && (
          <div className="mb-4 px-4 py-3 rounded-[8px] text-[13px]"
               style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', color: 'var(--danger)' }}>
            {error}
          </div>
        )}
        {!confirm ? (
          <button
            onClick={() => setConfirm(true)}
            className="btn btn-ghost"
            style={{ color: 'var(--danger)', borderColor: 'rgba(239,68,68,0.3)' }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--danger)' }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = 'rgba(239,68,68,0.3)' }}
          >
            <Trash2 size={14} /> Удалить аккаунт
          </button>
        ) : (
          <div className="space-y-3">
            <div className="p-4 rounded-[8px]" style={{ background: 'rgba(239,68,68,0.04)', border: '1px solid rgba(239,68,68,0.15)' }}>
              <p className="text-[13px] font-semibold mb-1" style={{ color: 'var(--danger)' }}>Вы уверены?</p>
              <p className="text-[13px]" style={{ color: 'var(--text-2)' }}>Это действие нельзя отменить. Аккаунт будет деактивирован немедленно.</p>
            </div>
            <div className="flex gap-3">
              <Button variant="destructive" onClick={deleteAccount} loading={deleting}>
                {!deleting && 'Да, удалить'}
              </Button>
              <Button variant="ghost" onClick={() => setConfirm(false)}>Отмена</Button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────
export default function AccountPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [tab, setTab] = useState<Tab>('profile')

  useEffect(() => {
    if (!localStorage.getItem('token')) { router.push('/login'); return }
    const t = searchParams.get('tab') as Tab | null
    if (t && TABS.some(x => x.id === t)) setTab(t)
  }, [router, searchParams])

  const CONTENT: Record<Tab, React.ReactNode> = {
    profile:       <ProfileTab />,
    security:      <SecurityTab />,
    notifications: <NotificationsTab />,
    language:      <LanguageTab />,
    danger:        <DangerTab />,
  }

  return (
    <div className="p-8" style={{ background: 'var(--bg)', minHeight: '100%' }}>
      {/* Page header */}
      <div className="mb-8">
        <p className="label mb-2">АККАУНТ</p>
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-[8px] flex items-center justify-center shrink-0"
               style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(124,58,237,0.18)' }}>
            <User size={18} style={{ color: 'var(--violet-text)' }} />
          </div>
          <div>
            <h1 className="text-[22px] font-bold" style={{ color: 'var(--text)' }}>Настройки аккаунта</h1>
            <p className="text-[13px]" style={{ color: 'var(--text-2)' }}>Профиль, безопасность, уведомления</p>
          </div>
        </div>
      </div>

      <div className="flex flex-col lg:flex-row gap-6">
        {/* Tab nav */}
        <nav className="lg:w-48 shrink-0">
          <div
            className="flex lg:flex-col gap-0.5 overflow-x-auto lg:overflow-visible p-1 rounded-[8px]"
            style={{ background: 'var(--bg)', border: '1px solid var(--surface)', scrollbarWidth: 'none' }}
          >
            {TABS.map(({ id, label, icon: Icon }) => {
              const isActive  = tab === id
              const isDanger  = id === 'danger'
              return (
                <button
                  key={id}
                  onClick={() => setTab(id)}
                  className="flex items-center gap-2.5 px-3 py-2.5 rounded-[8px] text-[13px] font-medium transition-all duration-200 whitespace-nowrap w-full"
                  style={isActive
                    ? { background: isDanger ? 'rgba(239,68,68,0.08)' : 'rgba(124,58,237,0.06)', color: isDanger ? 'var(--danger)' : 'var(--violet-text)' }
                    : { color: isDanger ? 'var(--danger)' : 'var(--text-2)', background: 'transparent' }
                  }
                  onMouseEnter={e => { if (!isActive) e.currentTarget.style.color = isDanger ? 'var(--danger)' : 'var(--text)' }}
                  onMouseLeave={e => { if (!isActive) e.currentTarget.style.color = isDanger ? 'var(--danger)' : 'var(--text-2)' }}
                >
                  <Icon
                    size={14}
                    className="shrink-0"
                    style={{ color: isActive ? (isDanger ? 'var(--danger)' : 'var(--violet-text)') : (isDanger ? 'var(--danger)' : 'var(--text-3)') }}
                  />
                  {label}
                </button>
              )
            })}
          </div>
        </nav>

        {/* Content */}
        <div className="flex-1 min-w-0">
          {CONTENT[tab]}
        </div>
      </div>
    </div>
  )
}
