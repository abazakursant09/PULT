// CONNECTION-UI — every string the seller sees about a marketplace connection.
//
// Two rules govern this file.
//
// 1. Nothing internal reaches the screen. The API client throws Errors whose message is the
//    backend's raw `detail` ("unknown scope: x", "HTTP 500") or, on a timeout, an untranslated
//    browser AbortError. None of that is for a seller. Every path here ends in a Russian sentence
//    we chose.
// 2. We never claim more than we know. Saving a key does NOT verify it — the backend marks a
//    connection "connected" the moment it is stored, before any marketplace call. Only
//    outcome === 'verified' means the key was actually proven to work.

/** How much a verification verdict actually tells us. Drives what the seller may do next. */
export type VerdictKind =
  | 'ok'          // proven to work
  | 'unchecked'   // saved, but we have no way to check it (Ozon feedbacks today)
  | 'broken'      // proven not to work — the seller must act
  | 'unknown'     // we could not find out right now; retrying is meaningful

const VERDICTS: Record<string, { kind: VerdictKind; text: string }> = {
  verified: { kind: 'ok', text: 'Ключ проверен — подключение работает.' },

  // Permanent, proven failures. The seller has to do something.
  invalid_credentials: { kind: 'broken', text: 'Ключ не подошёл. Проверьте, что скопировали его полностью.' },
  revoked: { kind: 'broken', text: 'Ключ отозван в кабинете маркетплейса. Выпустите новый.' },
  missing_scope: { kind: 'broken', text: 'У ключа нет доступа к отзывам. Выдайте это право в кабинете и подключите заново.' },
  tariff_restricted: { kind: 'broken', text: 'Тариф маркетплейса не даёт доступ к отзывам.' },
  ownership_conflict: { kind: 'broken', text: 'Этот кабинет уже подключён к другому аккаунту PULT.' },

  // Temporary. Nothing is wrong with the key as far as we know — ask again later.
  rate_limited: { kind: 'unknown', text: 'Маркетплейс временно ограничил запросы. Повторите проверку через несколько минут.' },
  timeout: { kind: 'unknown', text: 'Маркетплейс не ответил вовремя. Ключ сохранён — проверку можно повторить.' },
  marketplace_unavailable: { kind: 'unknown', text: 'Маркетплейс сейчас недоступен. Ключ сохранён — проверку можно повторить.' },

  // Our side could not make sense of the answer. Say so plainly; do not blame the seller's key.
  schema_mismatch: { kind: 'unknown', text: 'Не удалось разобрать ответ маркетплейса. Ключ сохранён, мы разберёмся.' },
  malformed_response: { kind: 'unknown', text: 'Не удалось разобрать ответ маркетплейса. Ключ сохранён, мы разберёмся.' },
  adapter_error: { kind: 'unknown', text: 'Не удалось разобрать ответ маркетплейса. Ключ сохранён, мы разберёмся.' },
  decrypt_failure: { kind: 'broken', text: 'Внутренняя ошибка хранения ключа. Обратитесь в поддержку.' },

  // We have no way to check this key at all. Absence of evidence, not evidence of failure —
  // so it does NOT block the seller from turning automation on.
  verification_unsupported: {
    kind: 'unchecked',
    text: 'Ключ сохранён, но проверить его сейчас нельзя — это станет известно при первой синхронизации отзывов.',
  },
}

const UNKNOWN_VERDICT = {
  kind: 'unknown' as VerdictKind,
  text: 'Результат проверки неизвестен. Ключ сохранён — проверку можно повторить.',
}

export function verdict(outcome: string | null | undefined) {
  if (!outcome) return UNKNOWN_VERDICT
  return VERDICTS[outcome] ?? UNKNOWN_VERDICT
}

/** Persisted per-scope state → one short label for the connection card. */
export function statusLabel(verificationStatus: string | null | undefined): string {
  switch (verificationStatus) {
    case 'verified': return 'Ключ проверен'
    case 'invalid_credentials': return 'Ключ не подошёл'
    case 'revoked': return 'Ключ отозван'
    case 'missing_scope': return 'Нет доступа к отзывам'
    case 'tariff_restricted': return 'Ограничено тарифом'
    case 'ownership_conflict': return 'Кабинет занят другим аккаунтом'
    default: return 'Ключ не проверен'
  }
}

/** True when the stored verdict PROVES the key cannot work — mirrors the backend gate. */
export function isBroken(verificationStatus: string | null | undefined): boolean {
  return ['invalid_credentials', 'revoked', 'missing_scope', 'ownership_conflict']
    .includes(verificationStatus || '')
}

/** A thrown API error → a sentence for a seller. The raw server text is never shown. */
export function requestErrorText(e: unknown): string {
  const m = e instanceof Error ? e.message : ''
  if (m.includes('HTTP 401') || m.includes('HTTP 403')) return 'Сессия истекла. Войдите заново.'
  if (m.includes('ozon_client_id')) return 'Для Ozon нужен Client-Id.'
  if (m.includes('token is required')) return 'Введите API-ключ.'
  if (m.includes('unknown marketplace')) return 'Этот маркетплейс пока не поддерживается.'
  if (m.includes('unknown scope')) return 'Внутренняя ошибка настройки доступа. Обратитесь в поддержку.'
  if (m.includes('not found') || m.includes('HTTP 404')) return 'Подключение не найдено.'
  if (m.includes('HTTP 422')) return 'Проверьте заполненные поля.'
  return 'Не удалось выполнить действие. Попробуйте позже.'
}

export const MP_NAME: Record<string, string> = {
  wildberries: 'Wildberries',
  ozon: 'Ozon',
  yandex: 'Яндекс Маркет',
}

export const mpName = (m: string): string => MP_NAME[m] || m
