/**
 * ПУЛЬТ Seller OS — данные под товар-центричную операционку.
 * Товар = атом. Поля: маркетплейс, прибыль, заказы, остаток, рейтинг, линзы.
 * Сейчас mock; ingest-ready — заменить getProducts/getOrders на api.* без правки UI.
 */
export type MP = 'wb' | 'ozon' | 'ym'
export type Sev = 'loss' | 'warn' | 'gain'
export const MP_NAME: Record<MP, string> = { wb: 'Wildberries', ozon: 'Ozon', ym: 'Я.Маркет' }

export interface Lens { sev: Sev; label: string; insightKey?: string }
export interface SellerProduct {
  id: string; n: string; m: MP; pos: number;
  pr: number;      // прибыль/мес
  o30: number;     // заказы за 30 дн
  stock: number;   // остаток, шт
  spd: number;     // скорость продаж, шт/дн
  rt: number;      // рейтинг
  L: Lens[];       // линзы
}

const LD: Record<string, { p: string; c: string; s: string; e: string }> = {
  'Реклама':   { p: 'Реклама в минус', c: 'CPM ×2.3 за 14 дней, конверсия та же', s: 'Снизить ставку 480 → 210 ₽', e: '+31 200 ₽/мес · ДРР 41%→18%' },
  'Отзывы':    { p: 'Рейтинг падает', c: '3 негатива о качестве — дефект партии', s: 'Ответить + претензия поставщику', e: 'стоп падения рейтинга' },
  'SEO':       { p: 'Упущен трафик', c: 'Нет ключа в заголовке — низкая позиция', s: 'Обновить SEO-заголовок', e: '+трафик, +позиции' },
  'Цена':      { p: 'Цена ниже рынка', c: 'Спрос держится — можно поднять', s: 'Поднять цену', e: '+прибыль без потери заказов' },
  'Документы': { p: 'Риск блокировки', c: 'Нет сертификата ЕАС', s: 'Загрузить документ', e: 'снятие риска' },
  'Возвраты':  { p: 'Возвраты выше нормы', c: 'Жалобы → возвраты', s: 'Разобрать причину', e: '−потери на возвратах' },
}
export const lensDetail = (label: string) => LD[label]

const PRODUCTS: SellerProduct[] = [
  { id: 'blend', n: 'Блендер PowerBlend 1200 Вт', m: 'wb', pos: 3, pr: -38400, o30: 142, stock: 64, spd: 4.7, rt: 4.7, L: [{ sev: 'loss', label: 'Реклама', insightKey: 'high_ad_spend' }, { sev: 'warn', label: 'Отзывы', insightKey: 'rating_drop' }, { sev: 'loss', label: 'Документы' }] },
  { id: 'grind', n: 'Кофемолка GrindPro', m: 'ozon', pos: 14, pr: 6100, o30: 88, stock: 14, spd: 4.6, rt: 4.3, L: [{ sev: 'loss', label: 'Отзывы', insightKey: 'rating_drop' }, { sev: 'warn', label: 'SEO', insightKey: 'seo_opportunity' }] },
  { id: 'boil', n: 'Чайник FastBoil 1.7 л', m: 'wb', pos: 1, pr: 54200, o30: 206, stock: 42, spd: 7.0, rt: 4.9, L: [{ sev: 'gain', label: 'Цена', insightKey: 'price_up' }, { sev: 'gain', label: 'Реклама' }] },
  { id: 'lunch', n: 'Ланчбокс HotMeal', m: 'wb', pos: 22, pr: -5800, o30: 54, stock: 120, spd: 1.8, rt: 4.5, L: [{ sev: 'warn', label: 'Возвраты' }] },
  { id: 'therm', n: 'Термос SteelKeep 750мл', m: 'ozon', pos: 9, pr: 18400, o30: 96, stock: 56, spd: 7.0, rt: 4.7, L: [{ sev: 'gain', label: 'SEO', insightKey: 'seo_opportunity' }] },
  { id: 'mug', n: 'Кружка CeramicPro', m: 'wb', pos: 5, pr: 9200, o30: 71, stock: 144, spd: 12, rt: 4.6, L: [{ sev: 'warn', label: 'Цена' }] },
  { id: 'juice', n: 'Соковыжималка JuiceMax', m: 'ozon', pos: 31, pr: -12000, o30: 33, stock: 88, spd: 1.1, rt: 4.2, L: [{ sev: 'loss', label: 'Реклама', insightKey: 'high_ad_spend' }] },
  { id: 'toast', n: 'Тостер CrispMax', m: 'ym', pos: 12, pr: -12000, o30: 28, stock: 60, spd: 0.9, rt: 4.6, L: [{ sev: 'loss', label: 'Реклама' }, { sev: 'warn', label: 'Отзывы' }] },
  { id: 'pan', n: 'Сковорода NonStick 28см', m: 'ozon', pos: 7, pr: 21300, o30: 84, stock: 200, spd: 6, rt: 4.8, L: [{ sev: 'gain', label: 'Цена' }] },
  { id: 'knife', n: 'Набор ножей SharpEdge', m: 'wb', pos: 4, pr: 31000, o30: 120, stock: 90, spd: 9, rt: 4.9, L: [{ sev: 'gain', label: 'SEO' }] },
  { id: 'board', n: 'Доска разделочная BambooPro', m: 'ym', pos: 18, pr: 4200, o30: 22, stock: 150, spd: 1.5, rt: 4.4, L: [{ sev: 'warn', label: 'SEO' }] },
  { id: 'scale', n: 'Весы кухонные PreciseFit', m: 'ozon', pos: 11, pr: 12800, o30: 60, stock: 70, spd: 4, rt: 4.7, L: [{ sev: 'gain', label: 'Реклама' }] },
]

export const mono = (n: string) => n.trim()[0]
export const rub = (v: number) => `${v < 0 ? '−' : '+'}${Math.abs(v).toLocaleString('ru-RU')} ₽`
export const daysLeft = (p: SellerProduct) => Math.round(p.stock / p.spd)
const sevRank: Record<Sev, number> = { loss: 0, warn: 1, gain: 2 }
export const worstLens = (p: SellerProduct) => p.L.slice().sort((a, b) => sevRank[a.sev] - sevRank[b.sev])[0]

export const getProducts = (): SellerProduct[] => PRODUCTS
export const byId = (id: string) => PRODUCTS.find(p => p.id === id)
export const byMp = (m: MP | 'all') => m === 'all' ? PRODUCTS : PRODUCTS.filter(p => p.m === m)

export interface MpSummary { m: MP; count: number; orders: number; profit: number }
export function marketplaceSummary(): MpSummary[] {
  return (['wb', 'ozon', 'ym'] as MP[]).map(m => {
    const list = PRODUCTS.filter(p => p.m === m)
    return { m, count: list.length, orders: list.reduce((a, p) => a + p.o30, 0), profit: list.reduce((a, p) => a + p.pr, 0) }
  })
}

export interface OrderRow { time: string; product: string; m: MP; sum: number; status: 'выкуплен' | 'в пути' | 'возврат' }
export const getOrders = (): OrderRow[] => [
  { time: '12:48', product: 'Чайник FastBoil', m: 'wb', sum: 2110, status: 'выкуплен' },
  { time: '12:41', product: 'Набор ножей SharpEdge', m: 'wb', sum: 3490, status: 'в пути' },
  { time: '12:33', product: 'Сковорода NonStick', m: 'ozon', sum: 1890, status: 'в пути' },
  { time: '12:20', product: 'Кофемолка GrindPro', m: 'ozon', sum: 2390, status: 'выкуплен' },
  { time: '12:04', product: 'Термос SteelKeep', m: 'ozon', sum: 1290, status: 'возврат' },
  { time: '11:52', product: 'Кружка CeramicPro', m: 'wb', sum: 690, status: 'выкуплен' },
  { time: '11:39', product: 'Тостер CrispMax', m: 'ym', sum: 2790, status: 'в пути' },
  { time: '11:21', product: 'Весы PreciseFit', m: 'ozon', sum: 990, status: 'выкуплен' },
]
