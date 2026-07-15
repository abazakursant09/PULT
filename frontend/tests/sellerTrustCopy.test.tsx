import { readFileSync } from 'fs'
import { join } from 'path'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TodayFocus from '@/components/decision-feed/TodayFocus'
import { api } from '@/lib/api'

// L1.1 — first-seller-trust copy alignment. After a CSV import the data IS received, so the flow
// must not imply otherwise. These guard the honest wording and that the genuine no-data / feed-empty
// states are untouched. Presentation only — no API/logic change.

const ROOT = join(__dirname, '..')
const read = (...p: string[]) => readFileSync(join(ROOT, ...p), 'utf-8')

describe('L1.1 — import copy drops the fixed-minute promise', () => {
  const src = read('app', 'dashboard', 'import', 'page.tsx')

  it('no longer promises a diagnosis "в течение минуты"', () => {
    expect(src).not.toMatch(/в течение минуты/)
  })

  it('states the honest "analysing, appears automatically" message', () => {
    // done screen + subtitle both point to the dashboard without a fixed-time guarantee
    expect(src).toMatch(/PULT анализирует их — рекомендации появятся на главной автоматически/)
    expect(src).toMatch(/PULT проанализирует данные и покажет рекомендации на главной/)
  })
})

describe('L1.1 — TodayFocus analysing state', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('no longer implies data is missing', () => {
    expect(read('components', 'decision-feed', 'TodayFocus.tsx')).not.toMatch(/когда PULT получит данные/)
  })

  it('renders the "received, analysing" copy when there is no top action yet', async () => {
    // top_action null (no diagnosis signal yet) — but TodayFocus only renders once data exists,
    // so the honest state is "получены / анализирует", not "no data".
    vi.spyOn(api.today, 'get').mockResolvedValue({ top_action: null } as never)
    vi.spyOn(api.presentation, 'getCards').mockResolvedValue({ cards: [] } as never)

    render(<TodayFocus />)
    expect(await screen.findByText(/Данные получены\. PULT анализирует ваш бизнес/)).toBeInTheDocument()
    // must not fall back to the old "waiting for data" wording
    expect(screen.queryByText(/когда PULT получит данные/)).not.toBeInTheDocument()
  })
})

describe('L1.1 — honest no-data / empty states preserved', () => {
  it('the first-run EmptyState still says "Нет данных для анализа"', () => {
    expect(read('app', 'dashboard', 'page.tsx')).toMatch(/Нет данных для анализа/)
  })

  it('the feed empty state is unchanged and never claims all-clear', () => {
    const raw = read('components', 'decision-feed', 'DecisionFeedEmptyState.tsx')
    expect(raw).toMatch(/PULT покажет решения здесь/)
    // strip comments — the doc-comment legitimately lists the forbidden phrases as a reminder;
    // the guard is about the rendered copy, not the note that documents it.
    const code = raw.replace(/\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '')
    for (const bad of [/всё хорошо/i, /проблем нет/i, /в порядке/i]) expect(code).not.toMatch(bad)
  })
})
