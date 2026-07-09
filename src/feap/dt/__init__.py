"""ДТ-ядро: детерминированная сборка, расчёт и проверка проекта Декларации на товары.

Модули (см. docs/dt_core_dev_plan.md):
- duty_parser     — строка ставки ЕТТ → структура
- customs_value   — Incoterms, распределение расходов, графы 45/46
- payments        — графа 47 (пошлина / акциз / НДС / сбор)
- assembler       — досье → graphs (header / items / totals)
- flk             — форматно-логический контроль (правила F-xx / W-xx)
- refdata         — снапшот справочных данных из БД для расчёта и ФЛК
- exporters       — XLSX (M1), PDF / XML R.055 (M2/M3)
- service         — оркестрация: load → assemble → validate → persist

Правило пакета: LLM здесь запрещён. Все числа воспроизводимы чистыми
функциями от входов (docs/dt_core_dev_plan.md §1).
"""
