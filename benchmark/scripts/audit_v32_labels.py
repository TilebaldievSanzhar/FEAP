"""Publish a versioned disposition of all 15 flagged labels, preserving original100."""
import hashlib
import json
from pathlib import Path

from benchmark.schema.test_case import GroundTruth, TestDataset

# Decisions refer to product descriptions and official tariff hierarchy, never to
# whether a particular model prediction would receive an additional point.
REVIEWS = [
    ('M04', '10', [4, 5, 6], None, 'Нет отношения длины зерна к ширине; точный десятизначный вариант риса не обоснован.'),
    ('M06', '16', [2, 5], '1602509501', 'Стерилизованная тушёнка в банках не относится к сырым продуктам 1602501000; это готовое мясо в герметичной таре, не солонина.'),
    ('M19', '41', [8, 9], None, 'Целая шкура не определяет, является ли кожа неспиленной лицевой, лицевым спилком или иным спилком.'),
    ('M20', '44', [10, 11], None, 'Обе версии 4407113409 и 4407113401 относятся к строганым лесоматериалам. Товар явно не строган. При сверке обнаружено потерянное родительское условие в name_full; исправлено отдельным патчем справочника после полных прогонов. Вероятная ветка 4407119401; карантин точного эталона до проверки отсутствия торцевых соединений. Вывод аудита v31 о достаточности замены влажности отозван.'),
    ('M34', '85', [11], '8501522001', 'Асинхронный трёхфазный двигатель 3 кВт относится к специальному листу асинхронных, не к остаточному 8501522009.'),
    ('M36', '85', [86, 87], None, 'Smart TV сам по себе не задаёт наличие встроенной аппаратуры записи или воспроизведения видео в смысле различающих условий веток.'),
    ('M39', '87', [118, 119, 120], None, 'Прицеп для перевозки грузов требует проверки 871639 вместо 871640; не указано состояние новый/бывший в эксплуатации для точного листа.'),
    ('H02', '84', [77], None, 'Комбинация печати, копирования и факса указывает на 844331 вместо 844332; основная функция цифрового копирования, отличающая 8443312000 от 8443318000, не установлена.'),
    ('H05', '04', [17, 18], None, 'Не подтверждены тип Гауда и необходимые параметры сыра. Жирность в сухом веществе не заменяет все условия точного листа.'),
    ('H10', '95', [6], None, 'Нужно подтвердить применение ветки консолей для внешнего экрана и исключение иных электронных игр; название PS5 не является достаточным документированным обоснованием исходного 9504500009.'),
    ('H12', '87', [116], None, 'Не указан тип подшипников, различающий листья велосипедов.'),
    ('H13', '94', [7], None, 'Нужно установить принадлежность к секционной кухонной мебели; комплект шкафов в разобранном виде сам по себе не обосновывает остаточный код.'),
    ('H20', '85', [20], None, 'Давление всасывания 5000 Па не является мощностью электродвигателя в ваттах; также не указан объём пылесборника.'),
    ('H29', '87', [75], '8704239209', 'Новый дорожный дизельный самосвал полной массой 25.2 т не относится к бывшим в эксплуатации 8704239809.'),
    ('H30', '85', [33], '8518220009', 'Два динамика в одном корпусе: комплект громкоговорителей, а не одиночный громкоговоритель 8518210000; бытовое назначение.'),
]


def main():
    original = Path('benchmark/data/cases/benchmark_100.jsonl')
    dataset = TestDataset.load_jsonl(original)
    cases = {c.id: c for c in dataset}
    sources = json.loads(Path('benchmark/data/reference/v32/manifest.json').read_text(encoding='utf-8'))
    reviews = []
    excluded = set()
    for short_id, group, pages, replacement, reason in REVIEWS:
        cid = 'BENCH-' + short_id
        case = cases[cid]
        reviews.append({
            'case_id': cid, 'old_code_10': case.ground_truth.code_10,
            'replacement_code_10': replacement,
            'disposition': 'corrected' if replacement else 'quarantined_exact10',
            'reason': reason, 'source': {**sources[group], 'pdf_pages_one_based': pages},
        })
        if replacement:
            case.ground_truth = GroundTruth.from_code10(replacement, case.ground_truth.section)
            case.source = 'Сверка структуры ЕТТ ЕАЭС 2026-09-21; аудит v32, не экспертное решение'
            case.source_url = sources[group]['url']
            case.notes = reason
            case.trap_codes = []  # Historical traps may now contain the corrected label.
        else:
            excluded.add(cid)
    reviewed = Path('benchmark/data/cases/v32_reviewed89.jsonl')
    TestDataset('v32_reviewed89', [c for c in dataset if c.id not in excluded]).save_jsonl(reviewed)
    report = {
        'scope': 'Only the 15 previously flagged cases were audited. Remaining labels are not certified. '
                 'Four source-based corrections and eleven exclusions are not model improvements. '
                 'This is a reproducible source review, not an expert customs ruling.',
        'original_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
        'reviewed_sha256': hashlib.sha256(reviewed.read_bytes()).hexdigest(),
        'corrected': 4, 'quarantined': 11, 'remaining_cases': len(dataset) - len(excluded),
        'reviews': reviews,
    }
    Path('benchmark/data/results/v32_label_audit.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
