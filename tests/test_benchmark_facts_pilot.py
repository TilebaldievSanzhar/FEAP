"""Пилот движка фактов: метрики маршрутизации к группам на игрушечных данных."""
from types import SimpleNamespace

from benchmark.scripts.facts_pilot import router_metrics


def case(cid, code):
    return SimpleNamespace(id=cid, gold=SimpleNamespace(classification=SimpleNamespace(code=code)))


def test_router_metrics_against_the_cascade():
    cases = [case('a', '9026100000'), case('b', '8414590000'), case('c', '0406100000'), case('d', '7318150000')]
    cascade = {'a': '84', 'b': '84', 'c': '04', 'd': None}          # ошибки каскада: a и d
    rankings = {'a': ['90', '84', '85'], 'b': ['85', '84', '90'], 'c': ['04', '19'], 'd': ['84', '83', '76', '73']}
    m = router_metrics(rankings, cases, cascade)
    assert m['n'] == 4 and m['cascade_errors'] == 2
    assert m['top1']['k'] == 2 and m['top3']['k'] == 3               # a, c в top-1; b в top-3; d нет
    assert m['rescue1']['k'] == 1 and m['rescue3']['k'] == 1         # a спасён, d — эталон 73 на 4-м месте
    assert m['gold_above_cascade']['k'] == 2                         # a: 90 выше 84; d: группы каскада нет
    assert m['damage1']['k'] == 1                                    # b: каскад прав, маршрутизатор — 85
    assert m['disagreements'] == 3 and m['router_right_when_disagreeing']['k'] == 1
    assert m['net_if_router_followed'] == 0.0                         # +1 (a) − 1 (b)
