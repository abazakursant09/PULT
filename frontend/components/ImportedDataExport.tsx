'use client'

import { useRef, useState } from 'react'
import { Button } from '@/components/ui/button'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export default function ImportedDataExport({ analysis = false }: { analysis?: boolean }) {
  const endpoint = analysis ? 'analysis' : 'imports'
  const filename = analysis ? 'pult-saved-analysis.json' : 'pult-imported-data.json'
  const title = analysis ? 'Выгрузка сохранённой аналитики' : 'Выгрузка импортированных данных'
  const buttonText = analysis ? 'Скачать сохранённую аналитику' : 'Скачать импортированные данные'
  const busy = useRef(false)
  const [pending, setPending] = useState(false)
  const [message, setMessage] = useState('')
  const [failed, setFailed] = useState(false)

  async function download() {
    if (busy.current) return
    busy.current = true
    setPending(true)
    setMessage('')
    setFailed(false)
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 60_000)
    try {
      const response = await fetch(`${API}/api/data/export/${endpoint}`, {
        credentials: 'include', cache: 'no-store', signal: controller.signal,
      })
      if (!response.ok) {
        const error = response.status === 401 || response.status === 403
          ? 'Сессия завершилась. Войдите в аккаунт повторно.'
          : response.status === 413
            ? 'Объём данных превышает лимит выгрузки. Обратитесь в поддержку.'
            : response.status === 429
              ? 'Слишком много запросов. Повторите выгрузку через 10 минут.'
              : 'Не удалось выгрузить данные. Попробуйте ещё раз.'
        throw new Error(error)
      }
      if (!response.headers.get('content-type')?.includes('application/json')) {
        throw new Error('Сервер вернул неожиданный формат. Файл не сохранён.')
      }
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      try { link.click() } finally {
        link.remove()
        // Give the browser time to start consuming the download before revocation.
        setTimeout(() => URL.revokeObjectURL(url), 30_000)
      }
      setMessage('Файл подготовлен и передан браузеру для скачивания.')
    } catch (error) {
      setFailed(true)
      setMessage(error instanceof Error && error.name !== 'AbortError' && !(error instanceof TypeError)
        ? error.message : 'Не удалось выгрузить данные. Проверьте соединение и попробуйте ещё раз.')
    } finally {
      clearTimeout(timer)
      busy.current = false
      setPending(false)
    }
  }

  return (
    <section aria-labelledby={`export-${endpoint}-title`} style={{ marginTop: 24 }}>
      <h2 id={`export-${endpoint}-title`} style={{ color: 'var(--text)', fontSize: 16, fontWeight: 700 }}>{title}</h2>
      <p style={{ color: 'var(--text-2)', fontSize: 13, margin: '8px 0 12px' }}>
        {analysis
          ? 'Сохранённые решения и сигналы 15 аналитических направлений в JSON. До 10 000 строк и 8 МиБ. Это не полный архив аккаунта и не снимок экранной ленты: измерения эффекта и история действий не включены.'
          : 'Товары, финансы, возвраты и контент карточек в JSON. До 10 000 строк и 8 МиБ. Это не полный архив аккаунта: рекомендации и исходные файлы не включены.'}
      </p>
      <Button type="button" variant="secondary" disabled={pending} aria-busy={pending} onClick={download}>
        {pending ? 'Подготавливаем файл…' : buttonText}
      </Button>
      {message && <p role={failed ? 'alert' : 'status'} style={{ color: 'var(--text-2)', marginTop: 8 }}>{message}</p>}
    </section>
  )
}
