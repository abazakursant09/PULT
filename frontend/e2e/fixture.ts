import { writeFileSync } from 'fs'
import path from 'path'

/** The smallest report that a real seller could upload and that PULT must diagnose.
 *
 * Dates are generated relative to the run, ending YESTERDAY, because that is what a seller's
 * report actually looks like — and because the dashboard decides whether to show the feed at
 * all from `has_data`, which reflects the most recent day's money. A file with hard-coded
 * dates would quietly rot into "no data" a few days after it was written.
 *
 * The verdict is arithmetic, not luck (backend/services/revenue/diagnosis_source.py):
 *
 *   6 distinct days  → the 2-window branch
 *   w1 = 1000×3 = 3000   (>= FLOOR_2W 100)
 *   w2 =  300×3 =  900   (< 3000 × FALL_STEP 0.95 → falling)
 *   ratio 900/3000 = 0.30 (<= COLLAPSE_RATIO 0.5 → "collapse", priority critical)
 *   cv of [300,300,300] = 0.0 (<= CV_MAX 0.6 → not a spike)
 *
 * The revenue producer has no choice but to emit a collapse.
 */
export function writeCollapseReport(dir: string): string {
  const header = 'Дата,Артикул WB,Название,Выручка,Комиссия,Логистика,Реклама,Чистая прибыль,Количество'
  const revenues = [1000, 1000, 1000, 300, 300, 300]

  const lines = revenues.map((revenue, i) => {
    const d = new Date()
    d.setDate(d.getDate() - (revenues.length - i))      // …ending yesterday
    const dd = String(d.getDate()).padStart(2, '0')
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const yyyy = d.getFullYear()
    return `${dd}.${mm}.${yyyy},ART-1001,Крем для рук,${revenue},100,50,20,${revenue - 170},10`
  })

  const file = path.join(dir, 'finance-collapse.csv')
  writeFileSync(file, [header, ...lines].join('\n') + '\n', 'utf-8')
  return file
}
