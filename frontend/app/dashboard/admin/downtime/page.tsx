'use client'

import { useState } from 'react'
import Link from 'next/link'
import {
  AlertTriangle, Server, Database, RefreshCw, Phone,
  CheckCircle, Circle, Clock, ExternalLink, Shield,
} from 'lucide-react'
import { Card } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Badge } from '@/components/ui/badge'

const A   = '#3B82F6'
const ABG = 'rgba(26,115,232,0.08)'
const ABR = 'rgba(26,115,232,0.18)'

interface Step {
  id:    number
  icon:  React.ReactNode
  title: string
  desc:  string
  cmds?: string[]
  links?: { label: string; href: string }[]
  critical?: boolean
}

const STEPS: Step[] = [
  {
    id: 1,
    icon: <Server size={16} />,
    title: 'Проверить сервер',
    desc: 'Убедитесь, что сервер запущен и отвечает.',
    cmds: [
      'ssh user@your-server-ip',
      'systemctl status nginx',
      'systemctl status uvicorn  # или pm2 status',
      'curl http://localhost:8000/api/health',
    ],
    critical: true,
  },
  {
    id: 2,
    icon: <RefreshCw size={16} />,
    title: 'Перезапустить сервисы',
    desc: 'Перезапустите backend и frontend, если они зависли.',
    cmds: [
      'systemctl restart uvicorn',
      'systemctl restart nginx',
      '# Или через PM2:',
      'pm2 restart all',
      'pm2 logs --lines 50',
    ],
    critical: true,
  },
  {
    id: 3,
    icon: <Database size={16} />,
    title: 'Проверить базу данных',
    desc: 'Проверьте подключение к БД и наличие свободного места.',
    cmds: [
      '# SQLite (дев):',
      'ls -lh business_pult.db',
      '# PostgreSQL (прод):',
      'psql -U postgres -c "SELECT 1"',
      'df -h  # проверить место на диске',
    ],
  },
  {
    id: 4,
    icon: <AlertTriangle size={16} />,
    title: 'Проверить логи',
    desc: 'Найдите причину падения в логах приложения.',
    cmds: [
      'journalctl -u uvicorn -n 100 --no-pager',
      'tail -f /var/log/nginx/error.log',
      'pm2 logs --err --lines 50',
    ],
  },
  {
    id: 5,
    icon: <Phone size={16} />,
    title: 'Связаться с хостингом',
    desc: 'Если проблема на стороне провайдера — создайте тикет.',
    links: [
      { label: 'Timeweb Support',   href: 'https://timeweb.com/ru/support/' },
      { label: 'Selectel Support',  href: 'https://selectel.ru/support/' },
      { label: 'Hetzner Support',   href: 'https://accounts.hetzner.com/support' },
      { label: 'DigitalOcean Docs', href: 'https://www.digitalocean.com/support/' },
    ],
  },
  {
    id: 6,
    icon: <Shield size={16} />,
    title: 'Уведомить пользователей',
    desc: 'Если даунтайм длится более 15 минут — опубликуйте статус.',
    cmds: [
      '# Временная заглушка (nginx):',
      'echo "503 — Технические работы" > /var/www/maintenance.html',
    ],
    links: [
      { label: 'Настроить статус-страницу', href: 'https://instatus.com' },
    ],
  },
]

export default function DowntimePage() {
  const [done, setDone] = useState<Set<number>>(new Set())
  const [copiedCmd, setCopiedCmd] = useState<string | null>(null)

  function toggleStep(id: number) {
    setDone(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  function copyCmd(cmd: string) {
    navigator.clipboard.writeText(cmd).then(() => {
      setCopiedCmd(cmd)
      setTimeout(() => setCopiedCmd(null), 1500)
    })
  }

  const progress = Math.round((done.size / STEPS.length) * 100)

  return (
    <main className="max-w-2xl mx-auto px-5 py-10 space-y-8">
      <Card className="p-6 sm:p-8" style={{ borderColor: 'rgba(220,38,38,0.2)', background: 'rgba(220,38,38,0.02)' }}>
        <div className="flex items-center gap-3 mb-3">
          <div className="w-10 h-10 rounded-2xl flex items-center justify-center shrink-0"
               style={{ background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.2)' }}>
            <AlertTriangle size={20} style={{ color: '#DC2626' }} />
          </div>
          <div>
            <h1 className="font-bold" style={{ fontSize: '1.375rem', color: '#202124' }}>
              Даунтайм-план
            </h1>
            <p className="text-xs" style={{ color: 'rgba(0,0,0,0.38)' }}>
              Только для администратора · /dashboard/admin/downtime
            </p>
          </div>
        </div>
        <p style={{ fontSize: '0.9375rem', color: '#5F6368', lineHeight: 1.65 }}>
          Чек-лист действий при падении сайта. Выполняйте шаги последовательно.
        </p>
      </Card>

      <Card className="p-5">
        <div className="flex items-center justify-between mb-3">
          <span className="text-sm font-medium" style={{ color: '#202124' }}>Прогресс</span>
          <span className="text-sm font-semibold" style={{ color: A }}>{done.size}/{STEPS.length} шагов</span>
        </div>
        <Progress value={progress} className="h-2" />
        {progress === 100 && (
          <p className="text-sm mt-2 font-medium" style={{ color: '#1A73E8' }}>
            ✅ Все шаги выполнены — сайт должен работать!
          </p>
        )}
      </Card>

      <div className="space-y-4">
        {STEPS.map(step => {
          const isDone = done.has(step.id)
          return (
            <Card
              key={step.id}
              className="p-5 sm:p-6 transition-all duration-200"
              style={{
                opacity: isDone ? 0.65 : 1,
                borderColor: isDone ? 'rgba(26,115,232,0.25)' : step.critical ? 'rgba(220,38,38,0.15)' : undefined,
              }}
            >
              <div className="flex items-start gap-4">
                <button
                  onClick={() => toggleStep(step.id)}
                  className="shrink-0 mt-0.5 transition-all duration-150"
                  title={isDone ? 'Отметить как незавершённый' : 'Отметить как выполненный'}
                >
                  {isDone
                    ? <CheckCircle size={22} style={{ color: '#1A73E8' }} />
                    : <Circle size={22} style={{ color: 'rgba(0,0,0,0.2)' }} />
                  }
                </button>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <div className="w-6 h-6 rounded-md flex items-center justify-center shrink-0"
                         style={{ background: ABG, color: A }}>
                      {step.icon}
                    </div>
                    <span className="font-semibold text-sm" style={{ color: isDone ? '#9CA3AF' : '#202124' }}>
                      Шаг {step.id}: {step.title}
                    </span>
                    {step.critical && !isDone && (
                      <Badge variant="destructive" className="text-[10px]">приоритет</Badge>
                    )}
                  </div>

                  <p className="text-sm mb-3" style={{ color: '#5F6368', lineHeight: 1.6 }}>
                    {step.desc}
                  </p>

                  {step.cmds && (
                    <div className="rounded-xl overflow-hidden mb-3"
                         style={{ background: '#1A1A1A', border: '1px solid rgba(0,0,0,0.06)' }}>
                      {step.cmds.map((cmd, i) => (
                        <div
                          key={i}
                          className="flex items-center justify-between group px-4 py-1.5"
                          style={{ borderBottom: i < step.cmds!.length - 1 ? '1px solid rgba(0,0,0,0.05)' : 'none' }}
                        >
                          <code className="text-xs font-mono flex-1"
                                style={{ color: cmd.startsWith('#') ? '#9A9897' : '#A8FFAE' }}>
                            {cmd}
                          </code>
                          {!cmd.startsWith('#') && (
                            <button
                              onClick={() => copyCmd(cmd)}
                              className="ml-3 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity text-xs px-2 py-0.5 rounded"
                              style={{ color: copiedCmd === cmd ? '#3B82F6' : '#9A9897', background: 'rgba(0,0,0,0.06)' }}
                            >
                              {copiedCmd === cmd ? '✓' : 'copy'}
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}

                  {step.links && (
                    <div className="flex flex-wrap gap-2">
                      {step.links.map(l => (
                        <a
                          key={l.href}
                          href={l.href}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-1 text-xs font-medium px-3 py-1.5 rounded-lg transition-all duration-150"
                          style={{ background: ABG, color: A, border: `1px solid ${ABR}` }}
                        >
                          {l.label} <ExternalLink size={10} />
                        </a>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </Card>
          )
        })}
      </div>

      <Card className="p-5">
        <div className="flex items-center gap-2 mb-3">
          <Clock size={15} style={{ color: A }} />
          <span className="font-semibold text-sm" style={{ color: '#202124' }}>Ожидаемое время восстановления</span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-sm">
          {[
            { issue: 'Перезапуск сервиса', time: '2–5 мин' },
            { issue: 'Проблема с БД',       time: '10–20 мин' },
            { issue: 'Проблема хостинга',   time: '30 мин – 2 ч' },
          ].map(r => (
            <div key={r.issue} className="px-3 py-2.5 rounded-xl"
                 style={{ background: '#F1F3F4', border: '1px solid rgba(26,115,232,0.1)' }}>
              <p className="font-medium" style={{ color: '#202124' }}>{r.time}</p>
              <p className="text-xs mt-0.5" style={{ color: 'rgba(0,0,0,0.38)' }}>{r.issue}</p>
            </div>
          ))}
        </div>
      </Card>

      <div className="text-center">
        <Link href="/dashboard" className="btn btn-ghost text-sm">
          ← Вернуться в Пульт
        </Link>
      </div>
    </main>
  )
}
