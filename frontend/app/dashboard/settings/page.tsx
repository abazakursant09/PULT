'use client'

import { useEffect, useState } from 'react'
import { Send, Check, AlertTriangle, Bell, Calendar, Clock, RefreshCw, Trash2, AlertCircle, ArrowLeft, Key } from 'lucide-react'
import { useRouter } from 'next/navigation'
import { api, type TelegramSettings } from '@/lib/api'
import { clearSession } from '@/lib/session'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'

const A   = 'var(--violet)'
const ABG = 'var(--violet-dim)'
const ABR = 'var(--line)'

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
  { key: 'notify_price_drop',      label: 'Резкое падение цены конкурента',desc: 'Конкурент снизил цену более чем на 10%', critical: true },
  { key: 'notify_negative_review', label: 'Новый негативный отзыв',        desc: 'Отзыв с оценкой 1–2 звезды' },
  { key: 'notify_trial_end',       label: 'Окончание пробного периода',    desc: 'За 3 дня до конца бесплатного доступа' },
]

export default function SettingsPage() {
  const router = useRouter()
  const [chatId,      setChatId]      = useState('')
  const [savedChatId, setSavedChatId] = useState<string | null>(null)
  const [chatSaving,  setChatSaving]  = useState(false)
  const [chatSaved,   setChatSaved]   = useState(false)
  const [chatError,   setChatError]   = useState('')
  const [testing,     setTesting]     = useState(false)
  const [testResult,  setTestResult]  = useState<'ok' | 'err' | null>(null)

  const [settings,     setSettings]     = useState<TelegramSettings | null>(null)
  const [settingsLoad, setSettingsLoad] = useState(true)
  const [saving,       setSaving]       = useState(false)
  const [saved,        setSaved]        = useState(false)

  const [deleteConfirm, setDeleteConfirm] = useState(false)
  const [deleting,      setDeleting]      = useState(false)
  const [deleteError,   setDeleteError]   = useState('')

  const [ozonKeyDate,   setOzonKeyDate]   = useState('')
  const [ozonKeySaved,  setOzonKeySaved]  = useState(false)
  const [ozonKeyWarn,   setOzonKeyWarn]   = useState<number | null>(null)

  function checkOzonKeyExpiry(dateStr: string) {
    if (!dateStr) { setOzonKeyWarn(null); return }
    const created = new Date(dateStr).getTime()
    const expiry  = created + 180 * 24 * 60 * 60 * 1000
    const daysLeft = Math.ceil((expiry - Date.now()) / (24 * 60 * 60 * 1000))
    setOzonKeyWarn(daysLeft <= 7 ? daysLeft : null)
  }

  useEffect(() => {
    Promise.all([api.telegram.getChatId(), api.telegram.getSettings()])
      .then(([cid, s]) => {
        setSavedChatId(cid.telegram_chat_id)
        setChatId(cid.telegram_chat_id ?? '')
        setSettings(s)
      })
      .catch(() => {})
      .finally(() => setSettingsLoad(false))

    const saved = localStorage.getItem('ozon_api_key_date') ?? ''
    setOzonKeyDate(saved)
    checkOzonKeyExpiry(saved)
  }, [])

  async function saveChatId() {
    setChatSaving(true); setChatError(''); setChatSaved(false)
    try {
      const res = await api.telegram.updateChatId(chatId.trim() || null)
      setSavedChatId(res.telegram_chat_id)
      setChatSaved(true)
      setTimeout(() => setChatSaved(false), 2500)
    } catch (e) {
      setChatError(e instanceof Error ? e.message : 'Ошибка')
    } finally {
      setChatSaving(false)
    }
  }

  async function sendTest() {
    setTesting(true); setTestResult(null)
    try {
      await api.telegram.test()
      setTestResult('ok')
    } catch {
      setTestResult('err')
    } finally {
      setTesting(false)
      setTimeout(() => setTestResult(null), 4000)
    }
  }

  function setField<K extends keyof TelegramSettings>(key: K, val: TelegramSettings[K]) {
    setSettings(s => s ? { ...s, [key]: val } : s)
  }

  function saveOzonKeyDate() {
    localStorage.setItem('ozon_api_key_date', ozonKeyDate)
    checkOzonKeyExpiry(ozonKeyDate)
    setOzonKeySaved(true)
    setTimeout(() => setOzonKeySaved(false), 2500)
  }

  async function deleteAccount() {
    setDeleting(true); setDeleteError('')
    try {
      await api.account.delete()
      clearSession()
      window.location.href = '/login'
    } catch (err: unknown) {
      setDeleteError(err instanceof Error ? err.message : 'Ошибка при удалении аккаунта')
      setDeleting(false)
      setDeleteConfirm(false)
    }
  }

  async function saveSettings() {
    if (!settings) return
    setSaving(true); setSaved(false)
    try {
      const updated = await api.telegram.updateSettings(settings)
      setSettings(updated)
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch {}
    finally { setSaving(false) }
  }

  return (
    <main className="max-w-2xl mx-auto px-5 py-10 space-y-8">
      <div>
        <button onClick={() => router.push('/dashboard/account?tab=notifications')}
                className="sm:hidden flex items-center gap-1.5 text-sm mb-4 rounded-lg px-2.5 py-1.5"
                style={{ color: 'var(--text-2)', border: '1px solid var(--line)' }}>
          <ArrowLeft size={13} /> Назад
        </button>
        <h1 className="font-bold" style={{ fontSize: '1.5rem', color: 'var(--text)' }}>
          Telegram-уведомления
        </h1>
        <p className="mt-1" style={{ fontSize: '0.9375rem', color: 'var(--text-3)' }}>
          Настройте получение уведомлений прямо в Telegram
        </p>
      </div>

      <Card className="p-6 sm:p-8">
        <div className="flex items-center gap-3 mb-5">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0" style={{ background: ABG, border: `1px solid ${ABR}` }}>
            <Send size={16} style={{ color: A }} />
          </div>
          <h2 className="font-semibold" style={{ fontSize: '1rem', color: 'var(--text)' }}>
            1. Подключить Telegram
          </h2>
        </div>

        <div className="rounded-xl p-4 mb-5 space-y-2 text-sm" style={{ background: 'var(--surface-h)', border: '1px solid var(--line)' }}>
          <p className="font-medium" style={{ color: 'var(--text)' }}>Как узнать свой Chat ID:</p>
          <ol className="space-y-1 pl-4 list-decimal" style={{ color: 'var(--text-2)' }}>
            <li>Откройте Telegram и найдите бота <strong style={{ color: A }}>@userinfobot</strong></li>
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
        {chatError && <p className="mt-2 text-sm text-destructive">{chatError}</p>}

        {savedChatId && (
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <span className="text-sm" style={{ color: 'var(--text-2)' }}>
              Chat ID: <code style={{ color: A }}>{savedChatId}</code>
            </span>
            <Button variant="ghost" size="sm" onClick={sendTest} loading={testing}>
              {!testing && (
                testResult === 'ok'
                  ? <><Check size={12} style={{ color: 'var(--violet)' }} /> Отправлено</>
                  : testResult === 'err'
                  ? <><AlertTriangle size={12} style={{ color: 'var(--danger)' }} /> Ошибка</>
                  : 'Тест'
              )}
            </Button>
          </div>
        )}
      </Card>

      <Card className="p-6 sm:p-8">
        <div className="flex items-center gap-3 mb-5">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0" style={{ background: ABG, border: `1px solid ${ABR}` }}>
            <Bell size={16} style={{ color: A }} />
          </div>
          <h2 className="font-semibold" style={{ fontSize: '1rem', color: 'var(--text)' }}>
            2. Типы уведомлений
          </h2>
        </div>

        {settingsLoad || !settings ? (
          <div className="flex justify-center py-8">
            <RefreshCw size={22} className="animate-spin text-muted-foreground" />
          </div>
        ) : (
          <div className="space-y-3">
            {EVENT_TYPES.map(({ key, label, desc, critical }) => (
              <div
                key={key}
                className="flex items-center justify-between gap-4 px-4 py-3.5 rounded-xl"
                style={{ background: 'var(--surface-h)', border: '1px solid var(--line)' }}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium" style={{ color: 'var(--text)' }}>{label}</span>
                    {critical && <Badge variant="destructive" className="text-[10px]">критично</Badge>}
                  </div>
                  <p className="text-xs mt-0.5" style={{ color: 'var(--text-3)' }}>{desc}</p>
                </div>
                <Switch
                  checked={settings[key] as boolean}
                  onCheckedChange={v => setField(key, v as TelegramSettings[typeof key])}
                />
              </div>
            ))}
          </div>
        )}
      </Card>

      {settings && (
        <Card className="p-6 sm:p-8">
          <div className="flex items-center gap-3 mb-5">
            <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0" style={{ background: ABG, border: `1px solid ${ABR}` }}>
              <Calendar size={16} style={{ color: A }} />
            </div>
            <h2 className="font-semibold" style={{ fontSize: '1rem', color: 'var(--text)' }}>
              3. Плановые отчёты
            </h2>
          </div>

          <div className="space-y-5">
            <div className="rounded-xl p-4" style={{ background: 'var(--surface-h)', border: '1px solid var(--line)' }}>
              <div className="flex items-center justify-between mb-3">
                <div>
                  <p className="text-sm font-medium" style={{ color: 'var(--text)' }}>Ежедневный отчёт</p>
                  <p className="text-xs mt-0.5" style={{ color: 'var(--text-3)' }}>Краткая сводка по товарам, отзывам и ценам</p>
                </div>
                <Switch checked={settings.daily_report} onCheckedChange={v => setField('daily_report', v)} />
              </div>
              {settings.daily_report && (
                <div className="flex items-center gap-2 mt-2">
                  <Clock size={13} style={{ color: 'var(--text-3)' }} />
                  <span className="text-xs" style={{ color: 'var(--text-2)' }}>Время отправки:</span>
                  <Input
                    type="time"
                    value={settings.daily_report_time}
                    onChange={e => setField('daily_report_time', e.target.value)}
                    className="h-8 text-sm" style={{ width: 110 }}
                  />
                </div>
              )}
            </div>

            <div className="rounded-xl p-4" style={{ background: 'var(--surface-h)', border: '1px solid var(--line)' }}>
              <div className="flex items-center justify-between mb-3">
                <div>
                  <p className="text-sm font-medium" style={{ color: 'var(--text)' }}>Еженедельная сводка</p>
                  <p className="text-xs mt-0.5" style={{ color: 'var(--text-3)' }}>Детальный отчёт за неделю: динамика, топ товары</p>
                </div>
                <Switch checked={settings.weekly_summary} onCheckedChange={v => setField('weekly_summary', v)} />
              </div>
              {settings.weekly_summary && (
                <div className="space-y-2 mt-2">
                  <div>
                    <p className="text-xs mb-1.5" style={{ color: 'var(--text-2)' }}>День недели:</p>
                    <div className="flex flex-wrap gap-1.5">
                      {DAYS.map(d => (
                        <button
                          key={d.value}
                          type="button"
                          onClick={() => setField('weekly_summary_day', d.value)}
                          className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-150"
                          style={{
                            background: settings.weekly_summary_day === d.value ? A : 'var(--surface-h)',
                            color:      settings.weekly_summary_day === d.value ? 'var(--text)' : 'var(--text-3)',
                            border:     `1px solid ${settings.weekly_summary_day === d.value ? A : 'var(--line)'}`,
                          }}
                        >
                          {d.label}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Clock size={13} style={{ color: 'var(--text-3)' }} />
                    <span className="text-xs" style={{ color: 'var(--text-2)' }}>Время:</span>
                    <Input
                      type="time"
                      value={settings.weekly_summary_time}
                      onChange={e => setField('weekly_summary_time', e.target.value)}
                      className="h-8 text-sm" style={{ width: 110 }}
                    />
                  </div>
                </div>
              )}
            </div>
          </div>
        </Card>
      )}

      {settings && (
        <div className="flex justify-end">
          <Button onClick={saveSettings} loading={saving}>
            {!saving && (saved ? <><Check size={15} /> Сохранено</> : <><RefreshCw size={15} /> Сохранить настройки</>)}
          </Button>
        </div>
      )}

      {/* Ozon API key expiry */}
      <Card className="p-6 sm:p-8">
        <div className="flex items-center gap-3 mb-5">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0" style={{ background: ABG, border: `1px solid ${ABR}` }}>
            <Key size={16} style={{ color: A }} />
          </div>
          <h2 className="font-semibold" style={{ fontSize: '1rem', color: 'var(--text)' }}>
            API-ключ Ozon
          </h2>
        </div>

        {ozonKeyWarn !== null && (
          <div className="mb-4 flex items-center gap-2 px-4 py-3 rounded-xl text-sm font-medium"
               style={{ background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.25)', color: 'var(--danger)' }}>
            <AlertTriangle size={14} />
            Ключ истекает через {ozonKeyWarn} дн. Обновите ключ.
          </div>
        )}

        <p className="text-sm mb-3" style={{ color: 'var(--text-2)' }}>
          Дата создания API-ключа Ozon. Ключ действует 180 дней — напомним за 7 дней до истечения.
        </p>
        <div className="flex gap-3 flex-wrap">
          <Input
            type="date"
            className="flex-1 min-w-0"
            value={ozonKeyDate}
            onChange={e => setOzonKeyDate(e.target.value)}
          />
          <Button onClick={saveOzonKeyDate} className="shrink-0">
            {ozonKeySaved ? <><Check size={13} /> Сохранено</> : 'Сохранить'}
          </Button>
        </div>
        {ozonKeyDate && (
          <p className="mt-2 text-xs" style={{ color: 'var(--text-2)' }}>
            Истекает: {new Date(new Date(ozonKeyDate).getTime() + 180 * 24 * 60 * 60 * 1000).toLocaleDateString('ru-RU')}
          </p>
        )}
      </Card>

      <Card className="overflow-hidden" style={{ borderColor: 'rgba(220,38,38,0.2)' }}>
        <div style={{ height: 3, background: 'rgba(220,38,38,0.5)', borderRadius: '4px 4px 0 0' }} />
        <div className="p-6">
          <div className="flex items-start gap-3 mb-4">
            <AlertCircle size={16} style={{ color: 'var(--danger)', flexShrink: 0, marginTop: 2 }} />
            <div>
              <p className="font-semibold" style={{ color: 'var(--text)', fontSize: '0.9375rem' }}>Удаление аккаунта</p>
              <p style={{ fontSize: '0.8125rem', color: 'var(--text-3)', marginTop: 3, lineHeight: 1.6 }}>
                Аккаунт будет помечен как удалённый. Email сохраняется и может быть использован
                для повторной регистрации с восстановлением реферальной истории.
              </p>
            </div>
          </div>

          {deleteError && (
            <div className="mb-4 px-4 py-3 rounded-xl" style={{ background: 'rgba(220,38,38,0.06)', border: '1px solid rgba(220,38,38,0.2)', color: 'var(--danger)', fontSize: '0.875rem' }}>
              {deleteError}
            </div>
          )}

          {!deleteConfirm ? (
            <Button variant="outline" onClick={() => setDeleteConfirm(true)}
                    style={{ color: 'var(--danger)', borderColor: 'rgba(220,38,38,0.3)' }}>
              <Trash2 size={14} /> Удалить аккаунт
            </Button>
          ) : (
            <div className="rounded-xl p-4" style={{ background: 'rgba(220,38,38,0.05)', border: '1px solid rgba(220,38,38,0.2)' }}>
              <p style={{ fontSize: '0.875rem', color: 'var(--danger)', fontWeight: 500, marginBottom: 12 }}>
                Вы уверены? Это действие нельзя отменить из панели управления.
              </p>
              <div className="flex gap-3">
                <Button variant="destructive" onClick={deleteAccount} loading={deleting}>
                  {!deleting && <><Trash2 size={13} /> Да, удалить</>}
                </Button>
                <Button variant="ghost" onClick={() => { setDeleteConfirm(false); setDeleteError('') }}>
                  Отмена
                </Button>
              </div>
            </div>
          )}
        </div>
      </Card>
    </main>
  )
}
