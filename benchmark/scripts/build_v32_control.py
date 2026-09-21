"""Freeze synthetic control goods before evaluation; never use predictions as labels."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import psycopg2

from benchmark.schema.test_case import GroundTruth, TestCase, TestDataset
from feap.config import settings

GOODS = [
    ('8501510001', 'Электродвигатель переменного тока трёхфазный асинхронный, номинальная выходная мощность ровно 750 Вт. Общепромышленный, не тяговый, для стационарного насоса.'),
    ('8501522001', 'Электродвигатель переменного тока трёхфазный асинхронный, номинальная выходная мощность 751 Вт. Для стационарного насоса, не для транспорта.'),
    ('8504408500', 'Отдельный статический инвертор постоянного тока в переменный, номинальная полная мощность 7,5 кВА. Промышленный, не предназначен для вычислительной или телекоммуникационной аппаратуры; аккумулятора и зарядного устройства нет.'),
    ('8504408700', 'Отдельный статический инвертор постоянного тока в переменный, номинальная полная мощность 8 кВА. Промышленный, не предназначен для вычислительной или телекоммуникационной аппаратуры; аккумулятора и зарядного устройства нет.'),
    ('8516310001', 'Электрический сушильный колпак для волос на стойке для парикмахерского салона, 1000 Вт, 220 В. Не ручной фен.'),
    ('8516310009', 'Ручной электрический фен для сушки волос, 1800 Вт, 220 В, бытовой. Без сушильного колпака.'),
    ('8516710000', 'Бытовая электрическая капельная кофеварка с нагревателем воды, стеклянным кофейником, мощность 900 Вт. Без кофемолки и без функции приготовления иных продуктов.'),
    ('8414510000', 'Настольный бытовой осевой вентилятор со встроенным электрическим двигателем номинальной выходной мощностью 40 Вт, питание 220 В. Без нагревателя, фильтра и распылителя воды.'),
    ('8509400000', 'Бытовой электрический погружной блендер для измельчения пищевых продуктов, встроенный электродвигатель 350 Вт. Без нагревателя и без функций приготовления пищи.'),
    ('4802562000', 'Немелованная бумага для печати и письма, без механических и химико-механических волокон, белая, 80 г/м². В прямоугольных листах 297×210 мм, формат А4, без покрытия и пропитки.'),
    ('4802568000', 'Немелованная бумага для печати и письма, без механических и химико-механических волокон, белая, 90 г/м². В прямоугольных листах 250×176 мм, без покрытия и пропитки.'),
    ('3924100000', 'Многоразовые столовые тарелки из полипропилена, без иных материалов. Для подачи еды в домашних условиях; не упаковочная тара.'),
    ('6302600000', 'Полотенца банные махровые тканые, состав 100% хлопок, размер 70×140 см, готовые подшитые изделия. Не трикотаж.'),
    ('6911100000', 'Фарфоровые обеденные тарелки для домашнего стола, глазурованные, круглые, диаметром 24 см. Не декоративные и не технические изделия.'),
    ('7013379900', 'Стаканы для питья из обычного натрий-кальциевого стекла, изготовлены механическим набором, без декора и резьбы. Стекло не упрочнённое, не хрусталь и не стеклокерамика, без ножки.'),
    ('8301100000', 'Замок висячий из стали с дужкой, механический, открывается ключом. Для ворот, поставляется отдельно с двумя ключами, не является частью двери.'),
]


def main():
    target = Path('benchmark/data/cases/v32_control16.jsonl')
    if target.exists():
        raise FileExistsError('Control dataset is locked; create a new version explicitly')
    rows = []
    connection = psycopg2.connect(settings.database_url_sync)
    try:
        with connection.cursor() as cursor:
            for idx, (code, description) in enumerate(GOODS, 1):
                cursor.execute('SELECT section_roman,name_full,name FROM tnved_tree WHERE code=%s AND is_leaf', (code,))
                node = cursor.fetchone()
                if node is None:
                    raise ValueError(f'Missing reference leaf {code}')
                rows.append(TestCase(
                    id=f'CTRL32-{idx:02}', product_description=description,
                    ground_truth=GroundTruth.from_code10(code, node[0]),
                    source='Synthetic fully specified goods; local reference tree, locked before evaluation',
                    notes=node[1] or node[2],
                ))
    finally:
        connection.close()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(''.join(r.model_dump_json() + '\n' for r in rows), encoding='utf-8')
    target.with_suffix('.manifest.json').write_text(json.dumps({
        'created_at': datetime.now(UTC).isoformat(), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
        'purpose': 'Locked synthetic control; not independently expert-labelled production goods. Do not tune on these outputs.',
        'n': len(rows),
    }, indent=2), encoding='utf-8')
    assert len(TestDataset.load_jsonl(target)) == 16


if __name__ == '__main__':
    main()
