'use client'

import { useState } from 'react'
import Link from 'next/link'
import { Headphones, Send, Check, Mail } from 'lucide-react'
import { MathCaptcha } from '@/components/MathCaptcha'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectOption } from '@/components/ui/select'
import { Accordion, AccordionItem, AccordionTrigger, AccordionContent } from '@/components/ui/accordion'
import { BlurFade } from '@/components/ui/blur-fade'
import { Badge } from '@/components/ui/badge'

const SUBJECTS = [
  { value: 'billing',   label: 'Оплата и подписка' },
  { value: 'technical', label: 'Техническая проблема' },
  { value: 'account',   label: 'Аккаунт и доступ' },
  { value: 'feature',   label: 'Предложение по функционалу' },
  { value: 'other',     label: 'Другое' },
]

const FAQ = [
  {
    q: 'Как подключить свой магазин к Бизнес-Пульту?',
    a: 'Сейчас Бизнес-Пульт работает в демо-режиме с тестовыми данными. Подключение через API маркетплейсов (Wildberries, Ozon, Яндекс Маркет) появится в ближайших обновлениях. Следите за уведомлениями в личном кабинете.',
  },
  {
    q: 'Безопасно ли передавать данные магазина?',
    a: 'Да. Все данные передаются по зашифрованному соединению (HTTPS). Мы не передаём ваши данные третьим лицам и не используем их в рекламных целях. Подробнее — в Политике конфиденциальности.',
  },
  {
    q: 'Можно ли отменить подписку в любой момент?',
    a: 'Да, вы можете отменить подписку в любой момент в настройках аккаунта. Доступ сохраняется до конца оплаченного периода. Возврат средств за неиспользованное время — по запросу в поддержку.',
  },
  {
    q: 'Что если ИИ даст неверную рекомендацию?',
    a: 'Рекомендации ИИ носят информационный характер и не являются финансовым советом. Принимайте решения самостоятельно. Если вы заметили явную ошибку — сообщите нам через форму выше, это поможет улучшить систему.',
  },
  {
    q: 'Как работает пробный период?',
    a: '14 дней бесплатно без привязки карты. В течение пробного периода доступны все функции тарифа «Мастер». По истечении срока аккаунт переходит в режим ограниченного доступа — данные сохраняются, новые анализы недоступны.',
  },
]

const STATUS_ITEMS = [
  { label: 'API', status: 'online' },
  { label: 'Дашборд', status: 'online' },
  { label: 'Аналитика', status: 'online' },
  { label: 'Уведомления', status: 'degraded' },
]

export default function SupportPage() {
  const [form,      setForm]      = useState({ name: '', email: '', subject: '', message: '' })
  const [sent,      setSent]      = useState(false)
  const [captchaOk, setCaptchaOk] = useState(false)

  const set = (k: keyof typeof form) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
      setForm(f => ({ ...f, [k]: e.target.value }))

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!captchaOk) return
    try {
      const raw     = localStorage.getItem('bp_support_tickets')
      const tickets = raw ? JSON.parse(raw) : []
      tickets.unshift({ ...form, id: Date.now(), date: new Date().toISOString() })
      localStorage.setItem('bp_support_tickets', JSON.stringify(tickets.slice(0, 50)))
    } catch {}
    setSent(true)
  }

  function handleReset() {
    setSent(false)
    setForm({ name: '', email: '', subject: '', message: '' })
    setCaptchaOk(false)
  }

  return (
    <div className="min-h-screen" style={{ background: '#F6F9FC' }}>

      {/* Nav */}
      <nav className="sticky top-0 z-50 bg-white/80 backdrop-blur-md border-b border-border/50">
        <div className="max-w-4xl mx-auto px-6 h-16 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center font-bold text-sm" style={{ background: '#1A73E8', color: 'white' }}>П</div>
            <span className="font-bold text-lg tracking-tight" style={{ color: '#0A2540' }}>ПУЛЬТ</span>
          </Link>
          <Link href="/" className="btn btn-ghost" style={{ padding: '8px 16px', fontSize: '0.875rem' }}>
            На главную
          </Link>
        </div>
      </nav>

      <div className="max-w-4xl mx-auto px-6 py-12">

        {/* Hero */}
        <BlurFade inView className="mb-10">
          <div className="flex flex-col sm:flex-row sm:items-center gap-4 mb-6">
            <div className="w-14 h-14 rounded-2xl flex items-center justify-center shrink-0 shadow-stripe" style={{ background: '#1A73E8' }}>
              <Headphones size={24} style={{ color: 'white' }} />
            </div>
            <div>
              <h1 className="font-bold text-4xl" style={{ color: '#0A2540', letterSpacing: '-0.03em' }}>Техподдержка</h1>
              <p className="text-muted-foreground mt-1">Ответим в течение рабочего дня · <a href="mailto:hello@biznes-pult.ru" className="hover:underline" style={{ color: '#1A73E8' }}>hello@biznes-pult.ru</a></p>
            </div>
          </div>

          {/* System status */}
          <div className="flex flex-wrap gap-2">
            {STATUS_ITEMS.map(({ label, status }) => (
              <div key={label} className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-white border border-border text-xs font-medium" style={{ color: '#425466' }}>
                <span className="w-1.5 h-1.5 rounded-full" style={{ background: status === 'online' ? '#22c55e' : '#f97316' }} />
                {label}
                <span style={{ color: status === 'online' ? '#22c55e' : '#f97316' }}>
                  {status === 'online' ? 'работает' : 'замедление'}
                </span>
              </div>
            ))}
          </div>
        </BlurFade>

        <div className="grid lg:grid-cols-[1fr_340px] gap-6">

          {/* Left column */}
          <div className="space-y-6">

            {/* Contact form */}
            <BlurFade inView delay={0.1}>
              <Card className="shadow-stripe">
                <CardHeader>
                  <CardTitle className="text-lg" style={{ color: '#0A2540' }}>Написать в поддержку</CardTitle>
                </CardHeader>
                <CardContent>
                  {sent ? (
                    <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
                      <div className="w-16 h-16 rounded-2xl flex items-center justify-center" style={{ background: '#1A73E8' }}>
                        <Check size={28} style={{ color: 'white' }} />
                      </div>
                      <p className="font-semibold text-lg" style={{ color: '#0A2540' }}>Сообщение отправлено!</p>
                      <p className="text-muted-foreground max-w-sm leading-relaxed">
                        Спасибо, мы свяжемся с вами в ближайшее время по адресу <strong>{form.email}</strong>.
                      </p>
                      <Button variant="ghost" onClick={handleReset} className="mt-2">
                        Отправить ещё одно сообщение
                      </Button>
                    </div>
                  ) : (
                    <form onSubmit={handleSubmit} className="space-y-4">
                      <div className="grid sm:grid-cols-2 gap-4">
                        <div className="space-y-1.5">
                          <Label>Имя</Label>
                          <Input required placeholder="Иван Петров" value={form.name} onChange={set('name')} />
                        </div>
                        <div className="space-y-1.5">
                          <Label>Email</Label>
                          <Input type="email" required placeholder="you@example.com" value={form.email} onChange={set('email')} />
                        </div>
                      </div>
                      <div className="space-y-1.5">
                        <Label>Тема</Label>
                        <Select required value={form.subject} onChange={set('subject')}>
                          <SelectOption value="">Выберите тему...</SelectOption>
                          {SUBJECTS.map(s => <SelectOption key={s.value} value={s.value}>{s.label}</SelectOption>)}
                        </Select>
                      </div>
                      <div className="space-y-1.5">
                        <Label>Сообщение</Label>
                        <Textarea required rows={5} placeholder="Опишите проблему подробнее..." value={form.message} onChange={set('message')} />
                      </div>
                      <MathCaptcha onValid={setCaptchaOk} />
                      <Button type="submit" disabled={!captchaOk} className="flex items-center gap-2">
                        <Send size={14} /> Отправить
                      </Button>
                    </form>
                  )}
                </CardContent>
              </Card>
            </BlurFade>

            {/* FAQ */}
            <BlurFade inView delay={0.2}>
              <Card className="shadow-stripe">
                <CardHeader>
                  <CardTitle className="text-lg" style={{ color: '#0A2540' }}>Частые вопросы</CardTitle>
                </CardHeader>
                <CardContent>
                  <Accordion type="single" collapsible>
                    {FAQ.map((item, i) => (
                      <AccordionItem key={i} value={String(i)}>
                        <AccordionTrigger value={String(i)} className="text-sm font-medium text-left py-4" style={{ color: '#0A2540' }}>
                          {item.q}
                        </AccordionTrigger>
                        <AccordionContent value={String(i)}>
                          <p className="text-sm leading-relaxed text-muted-foreground">{item.a}</p>
                        </AccordionContent>
                      </AccordionItem>
                    ))}
                  </Accordion>
                </CardContent>
              </Card>
            </BlurFade>
          </div>

          {/* Right column */}
          <div className="space-y-4">
            <BlurFade inView delay={0.15}>
              <Card className="shadow-stripe">
                <CardContent className="p-6">
                  <div className="w-10 h-10 rounded-xl flex items-center justify-center mb-4" style={{ background: 'rgba(26,115,232,0.1)' }}>
                    <Mail size={18} style={{ color: '#1A73E8' }} />
                  </div>
                  <h3 className="font-semibold text-sm mb-1" style={{ color: '#0A2540' }}>Email-поддержка</h3>
                  <p className="text-xs text-muted-foreground mb-3">Ответ в течение 24 часов в рабочие дни</p>
                  <a href="mailto:hello@biznes-pult.ru" className="text-sm font-medium hover:underline" style={{ color: '#1A73E8' }}>
                    hello@biznes-pult.ru
                  </a>
                </CardContent>
              </Card>
            </BlurFade>

            <BlurFade inView delay={0.2}>
              <Card className="shadow-stripe">
                <CardContent className="p-6">
                  <h3 className="font-semibold text-sm mb-3" style={{ color: '#0A2540' }}>Время работы</h3>
                  <div className="space-y-2 text-sm">
                    {[
                      { day: 'Пн — Пт', time: '9:00 — 19:00' },
                      { day: 'Сб',      time: '10:00 — 16:00' },
                      { day: 'Вс',      time: 'Выходной' },
                    ].map(({ day, time }) => (
                      <div key={day} className="flex justify-between">
                        <span className="text-muted-foreground">{day}</span>
                        <span className="font-medium" style={{ color: '#0A2540' }}>{time}</span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </BlurFade>

            <BlurFade inView delay={0.25}>
              <Card className="shadow-stripe overflow-hidden">
                <CardContent className="p-6">
                  <h3 className="font-semibold text-sm mb-3" style={{ color: '#0A2540' }}>Популярные статьи</h3>
                  <div className="space-y-2">
                    {['Начало работы с платформой', 'Подключение Wildberries API', 'Настройка уведомлений', 'Экспорт отчётов'].map(title => (
                      <a key={title} href="#" className="flex items-center gap-2 text-xs hover:underline" style={{ color: '#1A73E8' }}>
                        <span>→</span> {title}
                      </a>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </BlurFade>
          </div>
        </div>
      </div>
    </div>
  )
}
