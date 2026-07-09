# План разработки ДТ-ядра (модуль `feap.dt`)

**Дата:** 2026-07-05
**Основание:** `docs/dt_draft_spec.md` (спека), `docs/product_vision_v2.md` (стратегия),
`docs/kg_customs_filing_research.md` (ограничения среды КР).
**Решение зафиксировано:** подача/интеграция с ЕАИС — вне скоупа. Ядро = проверить,
посчитать, классифицировать, собрать → **экспорт** (XLSX, PDF, XML R.055, JSON).

---

## 1. Архитектурный принцип: ядро — это harness, LLM — по краям

Прямое применение иерархии доверия из `AGENT_ENGINEERING.md` §2
(`prompt < schema < runtime enforcement`) к домену ДТ:

```
                 ┌────────────────────────────────────────────────┐
   Документы ──► │  LLM-слой (SGR-схемы, структурный вывод)       │  ненадёжный вход,
   Описания  ──► │  extraction / typing / генерация текста 31     │  типизированный выход
                 └───────────────┬────────────────────────────────┘
                                 │ только данные, никогда — числа «на веру»
                 ┌───────────────▼────────────────────────────────┐
                 │  ДЕТЕРМИНИРОВАННОЕ ЯДРО (этот план)            │
                 │  duty_parser · customs_value · payments        │
                 │  assembler · ФЛК-движок · exporters            │
                 │  Decimal, ноль LLM, всё воспроизводимо         │
                 └───────────────┬────────────────────────────────┘
                                 │ findings, missing_parameters
                 ┌───────────────▼────────────────────────────────┐
                 │  LLM-советник (SGR): объяснить, предложить фикс│
                 │  человек принимает — ядро применяет            │
                 └────────────────────────────────────────────────┘
```

Правило: **любое число в графах 12/23/45/46/47 воспроизводимо чистой функцией
от входов**. LLM не считает деньги. LLM извлекает, объясняет и предлагает —
ядро проверяет и применяет. Это ровно паттерн «пиши код, который не доверяет
LLM» (`AGENT_ENGINEERING.md` §8.1), поэтому фундамент строим первым: он —
валидатор всего, что позже будут делать LLM-модули.

---

## 2. Состав ядра (пакет `src/feap/dt/`)

| Модуль | Ответственность | LLM |
|---|---|---|
| `schemas.py` | Pydantic-модели: досье, позиции, графы, findings | — |
| `duty_parser.py` | Строка ЕТТ → структура ставки (5 видов, 8 единиц, EUR/USD). Полное покрытие 254 уникальных форматов из leaves.json | — |
| `customs_value.py` | Incoterms-матрица, распределение расходов (largest-remainder), графы 45/46 | — |
| `payments.py` | Графа 47: пошлина (адвалор/специф/комбинир), акциз, НДС (база = ТС+пошлина+акциз), сбор; `missing_parameters` если не хватает физических величин | — |
| `assembler.py` | Досье → `graphs` JSONB (header / items / totals), листы (осн.+добавочные) | — |
| `flk.py` | Движок правил F-xx/W-xx поверх draft + `RefContext` (протокол; БД-независимый) | — |
| `refdata.py` | `DbRefContext`: существование кода, единицы, ставки, курсы — из PostgreSQL | — |
| `exporters/xlsx.py` | XLSX «по графам» (лист ДТ + листы товаров + платежи + проверки) | — |
| `exporters/xml_r055.py` | Экспорт XML по Решению ЕЭК № 75 — **этап M3**, сейчас каркас | — |
| `service.py` | Оркестрация: load → assemble → validate → persist draft (версии) | — |
| *(M2)* `extractors/` | SGR-извлечение из OCR-текста: инвойс/упаковочный/CMR/контракт | ✅ |
| *(M2)* `advisor.py` | Риск-советник + findings-fixer (предложения правок) | ✅ |
| *(M2)* `describe31.py` | Генератор описания графы 31 с fact-grounding валидатором | ✅ |

### Данные (миграция 009)

| Таблица | Зачем |
|---|---|
| `organizations` | Профиль своей организации + контрагенты + клиенты брокера. `kind: self/client/counterparty` |
| `product_catalog` | Память компании: артикул → утверждённый код (столп retention; API — следующий спринт) |
| `shipments` | Досье поставки: стороны, Incoterms, валюта, фрахт/страховка, метод распределения |
| `shipment_documents` | Файлы + типы + ссылка на ocr_job + extracted JSONB (M2) |
| `shipment_items` | Позиции: описание, код, количества, веса, цена, расчёты |
| `dt_drafts` | Версионируемые снапшоты `graphs` JSONB + `validation_report` JSONB (аудит-трейл; правка = новая версия) |
| `exchange_rates` | Курсы НБКР по датам (графа 23, EUR/USD для специфических ставок) |
| `usage_events` | **Метеринг с первого дня**: kind (classification/ocr/dt_draft/export), qty, org — биллинг потом просто читает |

Findings храним внутри `dt_drafts.validation_report` (JSONB), без отдельной
таблицы — они живут и умирают вместе с версией драфта (упрощение против спеки).

### API (`api/dt_router.py`, префикс `/api/v1`)

```
POST   /shipments                     создать досье (+позиции inline)
GET    /shipments/{id}                досье + позиции
PUT    /shipments/{id}                обновить шапку/позиции
DELETE /shipments/{id}
POST   /shipments/{id}/dt-draft       собрать проект ДТ (assemble+calc+validate) → draft
GET    /dt-drafts/{id}                графы + findings
GET    /dt-drafts/{id}/export.xlsx    файл
```

Всё синхронно — в детерминированном пути LLM нет, счёт за миллисекунды.
Классификация позиций в v1 — через существующий `/classify`/`/classify/batch`
(фронт кладёт `hs_code` в позицию); кнопка «классифицировать досье целиком»
(создание classification_job из shipment_items) — фаза 1.

### Мультиарендность и авторизация — как заложено

`org_id` стоит на всех новых таблицах **с первого дня** (ретрофит org-scoping —
самая дорогая ошибка). Пока auth нет: опциональный заголовок `X-Org-Id`, без
него — авто-создаваемая организация `default`. Когда появится auth (Track B),
`org_id` начнёт браться из токена — схема не меняется.

---

## 3. Фазы

### Фаза 0 — Фундамент (эта итерация) ✅ критерий: pytest зелёный
- duty_parser + customs_value + payments + assembler + flk + xlsx + модели +
  миграция 009 + API + фид НБКР. Тесты на каждый модуль, включая прогон
  парсера ставок по **всем** 13 279 листьям.

### Фаза 1 — Связка с классификацией и каталогом ✅ выполнена 2026-07-06
- `POST /shipments/{id}/classify` → batch-job (`job_type=shipment_classify`)
  по позициям без `hs_code`; worker пишет код обратно в `shipment_items`
  (`classification_ref=job_item:{id}`), ручные коды без `?force=true` не
  перезаписываются; по финализации джобы досье возвращается в `draft`
  (модуль `dt/classify_link.py`, хуки в `jobs/tasks.py`).
- `product_catalog` API (`/catalog*`: list/suggest/upsert/delete) +
  автоподстановка кода по SKU при создании/обновлении досье
  (`classification_ref=catalog:{id}`, `usage_count++`); LLM-коды попадают в
  каталог только через явный `import-from-shipment` (акт утверждения).
- Клонирование досье (`POST /shipments/{id}/clone`), список досье
  (`GET /shipments` c org-скоупом и счётчиками позиций).
- PDF-экспорт печатной формы (`export.pdf`, reportlab, водяной знак «ПРОЕКТ»,
  кириллический TTF: автопоиск или `DT_PDF_FONT_PATH`).

### Фаза 2 — LLM-слой на SGR ✅ выполнена 2026-07-07
- **Документы досье** (`dt/documents.py` + API `/shipments/{id}/documents*`):
  привязка текстом или ссылкой на файл OCR-job'а; extraction-результаты в
  `shipment_documents.extracted` JSONB (trace/датасет), без новой миграции.
- **Экстракторы** (`dt/extractors/`): типизация уровень 90 (Routing, порог
  `DT_DOCTYPE_CONFIDENCE_MIN` → `needs_kind`, тип выбирает человек) →
  извлечение уровень 91 (Cascade по типу: invoice/packing/cmr/contract/
  cert_origin; деньги строками, диспетчеризация кодом вместо Union в
  json_schema — anyOf в strict не везде поддержан) → `checks.py` пересчёт
  кодом (D-xx: Σ строк vs итог, построчная арифметика, нетто≤брутто) +
  инъекционные сенсоры (`sensors.py`, флаг — не блокировка).
- **Кросс-сверка** (`dt/crosscheck.py`): X-01..X-06 — чистая функция
  (severity warning: LLM-извлечённые данные не блокируют ready), встроена в
  `generate_draft` через общий `service.assemble_and_validate`; LLM уровень
  92 — только нечёткое сопоставление сторон/строк с `match_confidence`,
  ниже `DT_MATCH_CONFIDENCE_MIN` → «не сопоставлено».
- **Графа 31** (`dt/describe31.py`, уровень 93): факты строит код (позиция +
  классификация + каталог по SKU + строки доков с точным SKU);
  grounding-валидатор: facts_used ⊆ id, числа текста ⊆ чисел фактов, длина;
  reject → retry с фидбеком (`DT_G31_MAX_REJECTIONS`), после лимита
  `accepted=false`. Применяет человек (PUT).
- **Риск-советник** (`dt/advisor.py`, уровень 94): сигналы считает код
  (цена/кг vs медиана ×`DT_PRICE_OUTLIER_FACTOR`, нет нетто, нетто=брутто,
  низкий confidence); LLM формулирует + 31↔33 по именам кодов из БД;
  ответы вне выданных signal_id/позиций отбрасываются.
- **Findings-fixer** (`dt/fixer.py`, уровень 95): плоская routing-схема
  propose_value|ask_user|cannot_fix; whitelist полей, коэрция pydantic,
  верификация повторным `assemble_and_validate` (verified = finding исчез
  и error-count не вырос). Автоприменения нет.
- Метеринг: kinds `dt_extract`/`dt_crosscheck`/`dt_g31`/`dt_advise`/`dt_fix`;
  все LLM-эндпоинты — declarant+. 94 новых теста (fake LLM, без БД).
- Отложено: «пакет защиты» (§4) — после кастдева; org-скоуп OCR-job'ов при
  привязке документа — вместе с B2 (у OcrJob нет org_id).

### Фаза 3 — Форматы и жёсткие проверки ✅ выполнена 2026-07-09
- **Классификаторы Решения 378**: таблица `ref_classifiers` (миграция 011),
  сид `scripts/seed/classifiers.py` (42 значения: Incoterms/транспорт/
  процедуры/МОС — verified; виды документов/платежей, характер сделки —
  verified=false + TODO-verify по реальной ДТ КР). Страны/валюты не
  сидируются: неполный список дал бы ложные F-09, формат проверяют
  pydantic-схема и F-08.
- **Графы 24/36/44**: `ShipmentIn.deal_nature`, `ShipmentItemIn.preference`;
  графа 44 собирается из документов досье (Фаза 2) через
  `documents.build_g44_entries` + `DOC_KIND_CODES` (kind → код вида
  документа, TODO-verify; типы без кода идут в 44 без кода). Общие
  документы кладутся в g44 каждого товара; DocEntry несёт permit_status.
- **Полный ФЛК (слой 1 целиком)**: F-09 — коды по классификаторам через
  расширенный RefContext (`classifier_codes()`; не загружены → одно
  info-предупреждение, не тихий skip); F-10 — самопроверка арифметики
  графы 47 (адвалорные ставки пересчитываются, база НДС = ТС+пошлина+акциз,
  Σ по товарам = итогам; специфические/комбинированные не пересчитываются —
  их считает тот же payments.py); F-13 — преференция (36) требует
  cert_origin/permit в 44; F-14 — warning вместо error из спеки:
  предикат «мера applies (verified)» невыполним без хранимой верификации
  НТР, срабатывает по may_apply (`has_ntm_measures()` в снапшоте);
  F-15 — даты документов против meta.generated_at + «контракт раньше
  инвойса»; W-TULPAR — плохой статус проверки разрешительного (спека R-05,
  детерминированно из persisted permit_check).
- **XML R.055** (`exporters/xml_r055.py`, `GET /dt-drafts/{id}/export.xml`):
  stdlib ElementTree, элементы = графы 1:1, версия структуры
  `DT_XML_STRUCTURE_VERSION` в атрибуте; дисклеймер «проект» в документе.
  Официальный альбом Решения 75 и XSD в репо не входят (правки ежегодные,
  §12.4) — финальный маппинг подтверждается пилотом АРМ «Брокер»/KEDEN
  (вопрос №1 кастдева). XSD-хук: `DT_XML_XSD_PATH` + опциональный пакет
  xmlschema → ошибки валидации = 422, файл не отдаётся.
- **TULPAR** (`dt/tulpar.py`, `POST .../documents/{doc_id}/verify-permit`):
  формат номера — детерминированно и всегда; HTTP-поиск по
  `TULPAR_SEARCH_URL` (API swis2.trade.kg не документирован — клиент за
  узким интерфейсом, матчинг по вхождению номера в JSON до подтверждения
  формата на кастдеве); кэш `TULPAR_CACHE_HOURS` в
  `extracted.permit_check`; метерится только реальное обращение к реестру
  (kind=tulpar_check). Статусы: format_invalid/format_ok/found/not_found/
  unavailable (+зарезервированы status_mismatch/code_mismatch).
- Итого 56 роутов; 51 новый тест (326 в безопасных суитах). На живой БД:
  `alembic upgrade head` (009–011) + `python scripts/seed/classifiers.py`.

### Track B — auth/billing (параллельно, не блокирует ядро)
Рекомендация по последовательности:
1. **Сейчас (сделано в Фазе 0):** `organizations` + `org_id` везде +
   `usage_events` — метеринг копится с первого дня, даже без auth.
2. **B1 ✅ выполнен 2026-07-07:** `accounts` (argon2id) + `memberships`
   (owner/declarant/viewer) — миграция 010; JWT HS256 (PyJWT), организация
   в токене, членство/роль перечитываются на каждом запросе (мгновенная
   ревокация); `AUTH_MODE=optional` (dev: X-Org-Id/'default' как раньше) |
   `required`; API: /auth/register|login|me|switch-org,
   /orgs/current/members (owner-only, guard последнего owner'а).
   dt_router переведён на AuthContext: запись — declarant+, чужая
   организация → 404. Метеринг classification/ocr_document подключён к
   /classify, /classify/batch (org в job.params), /ocr/jobs —
   `feap/usage.py`; без ломающих изменений (в optional-режиме поведение
   прежнее). TODO B2: метеринг OCR-цепочки auto_classify (у OcrJob нет
   org_id) и org-скоуп на /jobs/*.
3. **B2 (при первых платящих):** тарифные планы как строки конфигурации,
   лимиты = агрегаты по `usage_events`, инвойсы вручную (B2B в КР нормально
   платит по счёту) → локальный PSP потом. Никакого Stripe-класса интеграций
   до подтверждённого спроса.

Логика: auth раньше биллинга, метеринг раньше всего, эквайринг — последним.

---

## 4. Где применяем SGR / context / harness engineering (Фаза 2)

Маппинг паттернов из `SGR_AGENT_ARCHITECTURE.md` и `AGENT_ENGINEERING.md`
на LLM-модули ДТ:

| LLM-модуль | SGR-паттерн | Harness-контроль (обязателен) |
|---|---|---|
| **Типизация документа** (инвойс/упаковочный/CMR/контракт/сертификат/не-документ) | Routing: `Union[InvoiceDoc, PackingDoc, CmrDoc, ContractDoc, CertOriginDoc, UnknownDoc]` с `kind: Literal[...]` | Confidence-порог; UnknownDoc → ручной выбор типа |
| **Извлечение полей** (по типу дока свой экстрактор — аналог task-type playbook §4.5) | Cascade: `document_profile` (сводка) → реквизиты → `line_items` (Cycle, `MinLen(1)`) → `totals_selfcheck` | **Пересчёт кодом**: Σ строк vs итог инвойса; расхождение → finding, не тихое доверие. Аналог claim-vs-action валидатора |
| **Кросс-сверка документов** | LLM только нечёткое сопоставление сущностей (стороны, наименования позиций между доками) через schema с `match_confidence` | Числа сверяет только код (X-01…X-06); LLM-матчинг ниже порога → «не сопоставлено», не догадка |
| **Генератор графы 31** | Cascade: `facts_available` → `missing_attributes` → `text_ru`; каждое утверждение в `facts_used: List[FactRef]` со ссылкой на источник (карточка/документ) | Grounding-валидатор: каждый FactRef существует в досье (аналог grounding read-proof §5.1 п.5); новые факты → reject + retry (лимит 2, как COMPLETION_MAX_REJECTIONS) |
| **Риск-советник** (цена/кг, соответствие 31↔33) | Cascade + Evidence Grounding: `signal` → `evidence: List[Evidence]` → `recommendation` | Пороговые сигналы считает код; LLM только формулирует и прикладывает evidence |
| **Findings-fixer** (предложить исправление по каждому error) | Routing: `Union[ProposeGraphValue, AskUser, AcknowledgeCannotFix]`; ProposeGraphValue = `{graph_ref, current, proposed, evidence, why}` | Применяет **человек** кнопкой; ядро валидирует proposed повторным прогоном ФЛК до показа. Никакого автоприменения |
| **«Пакет защиты»** (объяснение кода и стоимости для инспектора) | Cascade + обязательные `grounding_refs` | Все ссылки — на реальные документы досье; пустые refs → reject |
| **OCR-структуризатор** (уже есть, level 98) | Расширяется теми же схемами | Тот же totals-check |

Сквозные принципы (переносим из agent-доков как контракт Фазы 2):
- **Требование живёт на максимально жёстком уровне** (§8.2): Literal/constraints
  в схеме → runtime-валидатор → и только потом промпт.
- **Документы = данные, не инструкции.** Текст инвойса может содержать
  инъекцию — экстракторы не исполняют ничего из контента; на M2 добавить
  sensor-паттерны на OCR-текст (аналог §5.6).
- **Hot-reload промптов** — уже есть (Jinja2 registry), сохраняем.
- **Trace всё**: extraction-результаты и отклонённые генерации складываем в
  `shipment_documents.extracted` / draft history — это будущий датасет.
- **Никакого агентного цикла в пайплайне ДТ.** Wizard детерминирован; SGR-loop
  (NextStep/adaptive planning) прибережём для копайлота-чата по досье (H3).

---

## 5. Грамматика ставок ЕТТ (зафиксировано по данным)

254 уникальных формата в 13 279 листьях сводятся к:

```
''                                         → none (4 кода)
'{p}%'                                     → ad_valorem
'{p}%, но не менее {a} {CUR} за {UNIT}'    → combined_min  (max из двух)
'{p}% плюс {a} {CUR} за {UNIT}'            → combined_plus (сумма)
'{a} {CUR} за {UNIT}'                      → specific
CUR  ∈ {EUR, USD}
UNIT ∈ {1 кг · 1 л · 1 см3, мл · 1 т · 1 шт · 1 пар · 1 1000 шт · 1 м2}
```

Физическая база по единице: кг/т → вес нетто; л, см3 → доп. единица (объём);
шт, пар, 1000 шт → доп. количество; м2 → доп. площадь. Отсутствие базы →
`missing_parameters` (единый механизм needs_input).

---

## 6. Definition of Done Фазы 0

- [ ] `pytest tests/dt` зелёный; парсер ставок покрывает 100% реального корпуса
      (прогон по leaves.json — smoke-тест, «неизвестный формат» = провал).
- [ ] Через API создаётся досье с 2+ позициями, генерируется драфт: графы
      5/12/22/45/46/47 согласованы (ФЛК сам себя подтверждает), XLSX качается
      и открывается.
- [ ] Комбинированная ставка без веса нетто даёт `missing_parameters`, а не 0.
- [ ] Миграция 009 накатывается и откатывается.
- [ ] usage_events пишется при генерации драфта.

## 7. Открытые вопросы (тянутся из спеки, не блокируют Фазу 0)

1. Ставки сборов за таможенное оформление КР → пока настройка
   `DT_CUSTOMS_FEE_KGS` (пусто = строка сбора не формируется + warning).
2. Национальные коды видов платежей КР в графе 47 → константы с TODO-verify
   (сверить с реальной ДТ на кастдеве).
3. Импорт файлов в АРМ «Брокер» → вопрос №1 кастдева, влияет на приоритет XML.
