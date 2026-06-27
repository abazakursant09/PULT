# Canonical Surface Doctrine

> Normative project-level document. This is **not** a report — it is the binding
> reference for reviewing every future PR that adds or changes an executable Action.
> A PR that conflicts with this doctrine must be rejected.

## 1. Цель

Определить, какие контуры PULT могут быть **executable**, а какие по своей природе
являются **advisory**.

Граница проходит не по теме контура, а по природе рычага — по шагу **Apply**.
Executable-контур имеет честно-выводимый (observed, не сгенерированный) payload и
реальный write-API. Где этого нет — контур остаётся advisory, и это ограничение
**архитектурное, а не временно-техническое** (см. §3).

## 2. Canonical критерий Executable Surface

Контур считается executable **только если одновременно** существуют все шесть:

1. наблюдаемый **Signal** (observed-факт, не прогноз/не AI/не competitor data);
2. честно выводимый **payload** (из observed данных или read-only marketplace
   lookup — никогда сгенерированный/авторский контент);
3. **write API либо honest unavailable** (явный `CAPABILITY_NOT_SUPPORTED`, не
   подделка и не silent no-op);
4. **Measure** (наблюдаемая метрика baseline→after);
5. **Effect** (качественный band improved/unchanged/worsened/not_evaluated);
6. **Learning** (агрегация observed-исходов, marketplace-isolated).

При отсутствии **любого** пункта контур **не считается executable** и остаётся
advisory.

## 3. Классы ограничений

Каждое ограничение, блокирующее executable-статус, относится к одному из трёх
классов. Класс определяет, может ли ограничение когда-либо исчезнуть.

### Technical
Может исчезнуть при появлении API. Сегодня рычага нет, но он не противоречит
доктрине. Пример: `ad_set_state` на Yandex (нет адаптера), inventory-write
(пока impossible на всех МП). При появлении честного write-API контур/use-case
может стать executable.

### Doctrine
Нельзя нарушать **даже при наличии API**. Ограничение в принципах PULT: без AI,
без прогнозов, без `compute_recommendation`, без генерации/фабрикации контента.
Пример: SEO-копирайт, текст ответа на отзыв. Появление write-API **не снимает**
запрет — честный payload невыводим без человеческого ввода.

### Principle
Не относится к автоматическому исполнению в принципе. Сюда входят: человеческое
суждение и ответственность (Legal), мета-контур над другими контурами (Growth),
слой измерений (Finance), физический мир (fulfillment), самозапрет границы
(`review_boundary`). Эти контуры **навсегда advisory** — вопрос API к ним
неприменим.

## 4. Нормативная таблица

Классификация относится **к контуру в целом**, а не к отдельному use-case.
Контур из раздела Advisory/Mixed может содержать единичный executable use-case
только при **полном** соответствии всем шести критериям §2; это не меняет класс
контура.

### Executable

| Контур | Рычаги | Примечание |
|---|---|---|
| **Pricing** | `set_price`, `reduce_discount` | payload механически выводим; honest unavailable вне WB для скидки |
| **Advertising** | `ad_set_state`, `stop_auto_promotion` | WB/Ozon; Yandex — honest unavailable (Technical) |

### Advisory

| Контур | Обрыв цепочки | Класс |
|---|---|---|
| **Legal** | нет рычага и не должно быть (суждение/ответственность) | Principle |
| **Growth** | своего рычага нет — мета-слой над другими контурами | Principle |
| **Finance** | не сигнальный контур — substrate для Measure/Effect | Principle |
| **Operational Review** | `review_boundary` сам запрещает execution/recommendation | Principle |
| **Regime** | портфельный нарратив + weight-modifier, не рычаг | Principle |
| **Doctrine** (operational) | портфельная классификация, не рычаг | Principle |
| **физическая Warehouse / Logistics** | нет write-API (stock/replenish/shipment) | Technical + Principle (fulfillment — навсегда) |

### Mixed

Контур по классу advisory, но отдельные executable use-case допустимы **при
полном** соответствии §2.

| Контур | Executable use-case | Advisory часть |
|---|---|---|
| **Operations** | auto-promotion margin drain (Ozon: observe + `stop_auto_promotion`) | физическая логистика — advisory (нет write-API) |
| **SEO** | механическая правка уже-известного атрибута (без творчества) | контент (title/description/копирайт) — advisory (Doctrine) |
| **Reviews / Reputation** | — (API `review_reply` есть) | payload требует человеческого текста → заблокировано (Doctrine); см. `reputation-execution-deferred` |

> Классификация относится к контуру, не к use-case. Наличие одного executable
> use-case не делает контур executable.

## 5. Auto-Reject Rules

PR отклоняется автоматически, если он:

- использует **AI как Decision trigger**;
- использует **competitor data**;
- использует **forecast** как триггер Decision;
- создаёт **fabricated payload** (сгенерированный/авторский контент: SEO-текст,
  ответы на отзывы);
- делает **write при capability = impossible** вместо honest unavailable;
- даёт **Legal / Growth / Finance** собственный исполняемый Apply;
- превращает **operational_review** (или regime/doctrine/failure_forecast) в
  Signal-источник Decision;
- нарушает **marketplace isolation** (смешивает истории/агрегации разных МП).

## 6. Review Checklist

Перед принятием любого нового executable Action reviewer **обязан** проверить:

- [ ] Signal observed
- [ ] Payload observed-derived
- [ ] Capability честная (write API или honest unavailable)
- [ ] Measure существует
- [ ] Effect существует
- [ ] Learning существует
- [ ] Не нарушена Canonical Surface Doctrine (§2–§5)

Если хотя бы один пункт не выполнен — Action не принимается как executable.

## 7. Связанные документы

- [`canonical-spine-consolidation-audit.md`](./canonical-spine-consolidation-audit.md)
  — фаза 1 консолидации Spine, правило «один компонент за PR», что удалено/оставлено.
