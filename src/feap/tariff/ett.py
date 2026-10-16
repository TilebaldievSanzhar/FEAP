"""ЕТТ ЕАЭС из первоисточника: сводная редакция ЕЭК + примечания к ЕТТ.

Источник — страница ЕЭК eec.eaeunion.org/comission/department/catr/ett/:
- PDF по группам (ru.28_2022_17.08.2026.pdf): дата в имени — дата редакции;
- «Примечания к ЕТТ_08.10.2026.pdf»: временные ставки со сроками
  («123С) Ставка … 0 (ноль) % … применяется с даты вступления в силу Решения
  Совета ЕЭК от 9 июля 2026 г. № 76 по 31.10.2028 включительно»);
- в шапке страницы — перечень решений, которыми вносились изменения.

В PDF группы у листа: код, наименование, доп. единица и ставка; номер
примечания — надстрочный фрагмент после ставки («5» + «123С)»). Ставка на
дату = ставка примечания, если его период покрывает дату, иначе базовая.
Примечания «в отношении товаров, ввозимых в Российскую Федерацию» к КР не
применяются.

Модуль чистый: разбор работает на фрагментах текста с координатами (Span),
PyMuPDF нужен только extract_spans (импортируется лениво).
"""

from __future__ import annotations

import bisect
import datetime as dt
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin

from feap.dt.duty_parser import ALTERNATIVE_SUFFIX

#: Код ТН ВЭД в начале фрагмента; «+»/«*» перед кодом — пометки ЕТТ.
_CODE_RE = re.compile(r"^\s*[+*]?\s*(\d{4})(?:\s+(\d{2}))?(?:\s+(\d{3}|\d{2}))?(?:\s+(\d))?(?=\s|$)")
#: Номер примечания к ЕТТ: «123С)» (С кириллицей или латиницей).
_NOTE_RE = re.compile(r"^(\d{1,3})\s*[СC]\)$")
#: Фрагмент выше строки (надстрочный номер примечания) относится к ней.
_ROW_TOLERANCE = 3.0
_CODE_X_MAX = 120.0

_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


class EttParseError(ValueError):
    """Документ ЕТТ не разобран — данные не используются."""


@dataclass(frozen=True)
class Span:
    x0: float
    y0: float
    y1: float
    size: float
    flags: int
    text: str


@dataclass(frozen=True)
class EttRow:
    """Лист ЕТТ: ставка в нашем формате (как в tnved_tree.duty_rate) и примечания."""

    code: str
    rate: str
    rate_raw: str
    notes: tuple[str, ...] = ()
    unit: str | None = None


def note_key(raw: str) -> str:
    """«123C)» / «123С» → «123С» (кириллическая С, как в документе ЕЭК)."""
    m = re.match(r"^\s*(\d{1,3})\s*[СC]\)?\s*$", raw)
    if not m:
        raise EttParseError(f"номер примечания не разобран: {raw!r}")
    return f"{m.group(1)}С"


# ── PDF группы ───────────────────────────────────────────────────────────────

def extract_spans(pdf: bytes) -> list[list[Span]]:
    """PDF → фрагменты текста по страницам (PyMuPDF; в Docker-образе он есть)."""
    import pymupdf  # noqa: PLC0415 — тяжёлая зависимость только для разбора PDF

    pages: list[list[Span]] = []
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        for page in doc:
            spans: list[Span] = []
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for s in line["spans"]:
                        text = s["text"].strip()
                        if text:
                            x0, y0, _, y1 = s["bbox"]
                            spans.append(Span(x0, y0, y1, s["size"], s["flags"], text))
            pages.append(spans)
    return pages


def _columns(spans: list[Span]) -> tuple[float, float, float] | None:
    """Границы колонок по шапке таблицы: (низ шапки, доп. единица, ставка).

    None — на странице нет шапки таблицы: это вводные примечания к группе,
    где коды встречаются в тексте («В подсубпозициях 2008 50 110 0 – …»).
    """
    unit = next((s for s in spans if s.text.startswith("Доп.")), None)
    rate = next((s for s in spans if s.text.startswith("Ставка")), None)
    if unit is None or rate is None:
        return None
    bottom = max((s.y1 for s in spans if s.text.startswith("в долларах")), default=rate.y1)
    return bottom, unit.x0 - 8, rate.x0 - 12


def _join_rate(frags: list[list], sups: list[tuple[float, float, str]]) -> str:
    """Фрагменты ставки по строкам; надстрочная степень («2» у «м») — в конец
    фрагмента слева на той же строке."""
    frags = [list(f) for f in frags]
    for sy, sx, text in sups:
        same_line = [f for f in frags if abs(f[0] - sy) <= 6 and f[1] < sx]
        if same_line:
            max(same_line, key=lambda f: f[1])[2] += text
        else:
            frags.append([sy, sx, text])
    ordered = sorted(frags, key=lambda f: (round(f[0]), f[1]))
    return re.sub(r"\s+", " ", " ".join(f[2] for f in ordered)).strip()


def parse_group(pages: list[list[Span]], group: str) -> dict[str, EttRow]:
    """Фрагменты PDF группы → листья с базовой ставкой и номерами примечаний."""
    parts: dict[str, dict[str, list]] = {}
    carry: str | None = None  # строка, продолжающаяся на следующей странице
    for spans in pages:
        cols = _columns(spans)
        if cols is None:
            continue
        bottom, unit_x, rate_x = cols
        # Надстрочные фрагменты первой строки стоят чуть выше её базовой линии.
        body = [s for s in spans if s.y0 > bottom - 1]
        size = max((s.size for s in body), default=13.0)
        anchors: list[tuple[float, str | None]] = []
        for s in body:
            if s.x0 >= _CODE_X_MAX:
                continue
            m = _CODE_RE.match(s.text.replace(" ", " "))
            if not m:
                continue
            digits = "".join(g for g in m.groups() if g)
            code = digits if len(digits) == 10 and digits.startswith(group) else None
            anchors.append((s.y0, code))
            if code:
                parts.setdefault(code, {"rate": [], "sup": [], "notes": [], "unit": []})
        anchors.sort()
        ys = [a[0] for a in anchors]
        for s in sorted(body, key=lambda s: (s.y0, s.x0)):
            if s.x0 < unit_x:
                continue
            i = bisect.bisect_right(ys, s.y0 + _ROW_TOLERANCE) - 1
            code = anchors[i][1] if i >= 0 else carry
            if code is None:
                continue
            row = parts[code]
            superscript = s.size < size * 0.8 or bool(s.flags & 1)
            if superscript and _NOTE_RE.match(s.text):
                row["notes"].append(note_key(s.text))
            elif s.x0 >= rate_x:
                (row["sup"] if superscript else row["rate"]).append((s.y0, s.x0, s.text))
            else:
                row["unit"].append(s.text)
        if anchors:
            carry = anchors[-1][1]

    rows: dict[str, EttRow] = {}
    for code, p in parts.items():
        raw = _join_rate(p["rate"], p["sup"])
        rows[code] = EttRow(
            code=code, rate=canonical_rate(raw), rate_raw=raw,
            notes=tuple(dict.fromkeys(p["notes"])),
            unit=" ".join(p["unit"]).strip() or None,
        )
    return rows


# ── Ставка: текст ЕТТ → формат tnved_tree.duty_rate (dt.duty_parser) ─────────

_UNITS: tuple[tuple[str, str], ...] = (
    (r"1000\s*кг", "1 т"),
    (r"1000\s*шт", "1 1000 шт"),
    (r"1\s*см(?:3|³)?\s*объема\s*двигателя", "1 см3, мл"),
    (r"1\s*пару", "1 пар"),
    (r"1\s*кг", "1 кг"),
    (r"1\s*л", "1 л"),
    (r"1\s*шт", "1 шт"),
    (r"1\s*м2", "1 м2"),
)
_CURRENCY = r"(?:евро|долларов\s+США|доллара\s+США|доллар\s+США)"


def _num(raw: str) -> str:
    return raw.replace(",", ".").strip()


def _cur(raw: str) -> str:
    return "EUR" if raw.startswith("евро") else "USD"


def _unit(raw: str) -> str | None:
    for pattern, unit in _UNITS:
        if re.fullmatch(pattern, raw.strip()):
            return unit
    return None


def canonical_rate(raw: str) -> str:
    """«50, но не менее 1 евро за 1 кг» → «50%, но не менее 1 EUR за 1 кг».

    Формат, который понимает dt.duty_parser. Нестандартные ставки («15, либо
    12,5, но не менее …, в зависимости от того, какая … ниже») возвращаются как
    есть — парсер ставок отдаст needs_input «нестандартная ставка», а не 0.
    """
    s = re.sub(r"\s+", " ", raw).strip()
    if not s:
        return ""
    if re.fullmatch(r"\d+(?:,\d+)?", s):
        return f"{_num(s)}%"
    m = re.fullmatch(rf"(\d+(?:,\d+)?),? но не менее (\d+(?:,\d+)?) ({_CURRENCY}) за (.+)", s)
    if m and (unit := _unit(m.group(4))):
        return f"{_num(m.group(1))}%, но не менее {_num(m.group(2))} {_cur(m.group(3))} за {unit}"
    m = re.fullmatch(rf"(\d+(?:,\d+)?) ?%? плюс (\d+(?:,\d+)?) ({_CURRENCY}) за (.+)", s)
    if m and (unit := _unit(m.group(4))):
        return f"{_num(m.group(1))}% плюс {_num(m.group(2))} {_cur(m.group(3))} за {unit}"
    m = re.fullmatch(rf"(\d+(?:,\d+)?) ({_CURRENCY}) за (.+)", s)
    if m and (unit := _unit(m.group(3))):
        return f"{_num(m.group(1))} {_cur(m.group(2))} за {unit}"
    m = re.fullmatch(
        rf"(\d+(?:,\d+)?),? либо (\d+(?:,\d+)?),? но не менее (\d+(?:,\d+)?) ({_CURRENCY}) за (.+?),? "
        rf"{ALTERNATIVE_SUFFIX}", s,
    )
    if m and (unit := _unit(m.group(5))):
        return (f"{_num(m.group(1))}%, либо {_num(m.group(2))}%, но не менее {_num(m.group(3))} "
                f"{_cur(m.group(4))} за {unit}, {ALTERNATIVE_SUFFIX}")
    return s


# ── Примечания к ЕТТ ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class NotePeriod:
    rate: str                         # формат tnved_tree.duty_rate
    valid_from: dt.date | None        # None — «с даты вступления в силу решения»
    valid_to: dt.date | None
    from_decision: str | None = None  # «Решения Совета ЕЭК от 9 июля 2026 г. № 76»
    scope: str = "all"                # all | ru (только ввоз в Российскую Федерацию)


@dataclass(frozen=True)
class EttNote:
    note_id: str
    kind: str                         # rate | void | other
    periods: tuple[NotePeriod, ...] = ()
    text: str = ""


_DATE = r"(\d{2}\.\d{2}\.\d{4}|\d{1,2} [а-я]+ \d{4} г\.)"
_RATE_EXPR = (
    rf"в размере (\d+(?:,\d+)?)(?: \([а-я]+\))? ?% от таможенной стоимости"
    rf"(?:,? (но не менее|плюс) (\d+(?:,\d+)?) ({_CURRENCY}) за ([^,]+?)(?=,| применяется))?"
)


def _date(raw: str) -> dt.date:
    raw = raw.strip()
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", raw):
        return dt.datetime.strptime(raw, "%d.%m.%Y").date()
    m = re.fullmatch(r"(\d{1,2}) ([а-я]+) (\d{4}) г\.", raw)
    if not m or m.group(2) not in _MONTHS:
        raise EttParseError(f"дата не разобрана: {raw!r}")
    return dt.date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))


def _note_rate(m: re.Match) -> str:
    pct = _num(m.group(1))
    if m.group(2):
        unit = _unit(m.group(5))
        if unit is None:
            raise EttParseError(f"единица ставки не разобрана: {m.group(5)!r}")
        joiner = ", но не менее" if m.group(2) == "но не менее" else " плюс"
        return f"{pct}%{joiner} {_num(m.group(3))} {_cur(m.group(4))} за {unit}"
    return f"{pct}%"


def _period(rate: str, segment: str) -> NotePeriod | None:
    if "ввозимых в Российскую Федерацию" in segment:
        start = re.search(rf"\bс {_DATE}", segment)
        return NotePeriod(rate, _date(start.group(1)) if start else None, None, scope="ru")
    m = re.search(rf"применяется с {_DATE} по {_DATE} включительно", segment) or \
        re.search(rf"– с {_DATE} по {_DATE} включительно", segment)
    if m:
        return NotePeriod(rate, _date(m.group(1)), _date(m.group(2)))
    m = re.search(rf"с даты вступления в силу (Решения .+? № \d+) по {_DATE} включительно", segment)
    if m:
        return NotePeriod(rate, None, _date(m.group(2)), from_decision=m.group(1))
    return None


def parse_notes(text: str) -> dict[str, EttNote]:
    """Текст «Примечаний к ЕТТ» → примечания со ставками и сроками."""
    flat = re.sub(r"\s+", " ", text)
    chunks = re.split(r"(?=(?<![\d.])\d{1,3} ?[СC]\) )", flat)
    notes: dict[str, EttNote] = {}
    for chunk in chunks:
        m = re.match(r"(\d{1,3}) ?[СC]\) (.*)", chunk.strip())
        if not m:
            continue
        nid, body = f"{m.group(1)}С", m.group(2).strip()
        if nid in notes:
            raise EttParseError(f"примечание {nid} встречается дважды")
        if "утратило силу" in body[:60]:
            notes[nid] = EttNote(nid, "void", text=body)
            continue
        rates = list(re.finditer(_RATE_EXPR, body))
        if not body.startswith("Ставка ввозной таможенной пошлины") or not rates:
            notes[nid] = EttNote(nid, "other", text=body)
            continue
        periods: list[NotePeriod] = []
        ok = True
        for i, rm in enumerate(rates):
            end = rates[i + 1].start() if i + 1 < len(rates) else len(body)
            # «…включительно, с 01.10.2022 – в размере 8 %…»: дата перед ставкой
            # принадлежит следующему сегменту.
            seg_start = rm.start()
            if i > 0:
                lead = re.search(rf"с {_DATE} – $", body[rates[i - 1].end():rm.start()])
                if lead:
                    seg_start = rates[i - 1].end() + lead.start()
            segment = body[seg_start:end]
            try:
                period = _period(_note_rate(rm), segment)
            except EttParseError:
                period = None
            if period is None:
                ok = False
                break
            periods.append(period)
        notes[nid] = EttNote(nid, "rate" if ok else "other", tuple(periods) if ok else (), body)
    return notes


# ── Страница ЕЭК ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EttFile:
    url: str
    filename: str
    version: dt.date | None


@dataclass(frozen=True)
class EttPage:
    groups: dict[str, EttFile]
    notes: EttFile | None
    decisions: tuple[str, ...] = field(default_factory=tuple)


def parse_page(html: str, page_url: str) -> EttPage:
    groups: dict[str, EttFile] = {}
    notes: EttFile | None = None
    for href in re.findall(r'href="([^"]+\.pdf)"', html):
        name = unquote(href.rsplit("/", 1)[-1])
        url = urljoin(page_url, href)
        m = re.fullmatch(r"ru\.(\d{2})_\d{4}(?:_(\d{2}\.\d{2}\.\d{4}))?\.pdf", name)
        if m:
            version = dt.datetime.strptime(m.group(2), "%d.%m.%Y").date() if m.group(2) else None
            groups[m.group(1)] = EttFile(url, name, version)
            continue
        m = re.fullmatch(r"Примечания к ЕТТ_(\d{2}\.\d{2}\.\d{4})\.pdf", name)
        if m:
            version = dt.datetime.strptime(m.group(1), "%d.%m.%Y").date()
            if notes is None or (notes.version or dt.date.min) < version:
                notes = EttFile(url, name, version)
    text = re.sub(r"<[^>]+>", " ", html).replace("&nbsp;", " ")
    decisions = tuple(dict.fromkeys(
        f"{d} № {n}" for d, n in re.findall(r"от\s*(\d{2}\.\d{2}\.\d{4})\s*№\s*(\d+)", text)
    ))
    return EttPage(groups, notes, decisions)


# ── Ставка на дату ───────────────────────────────────────────────────────────

#: Версия файла группы без даты в имени — исходная редакция ЕТТ 2022 года.
BASE_EDITION = dt.date(2022, 1, 1)


@dataclass(frozen=True)
class EffectiveRate:
    rate: str
    source: str                       # ett_base | ett_note
    version: dt.date
    note_id: str | None = None
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None
    from_decision: str | None = None
    issue: str | None = None          # примечание не разобрано — ставку не берём

    def to_json(self) -> dict:
        return {
            "rate": self.rate, "source": self.source, "version": self.version.isoformat(),
            "note_id": self.note_id,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "from_decision": self.from_decision, "issue": self.issue,
        }


def effective_rate(
    *,
    base_rate: str,
    note_ids: tuple[str, ...] | list[str],
    version: dt.date | None,
    notes: dict[str, EttNote],
    day: dt.date,
) -> EffectiveRate | None:
    """Ставка ЕТТ на день. None — день раньше редакции, по которой разобрана группа.

    Примечание «с даты вступления в силу решения» без даты начала считается
    действующим с даты редакции группы, в которой оно появилось (ЕЭК выпускает
    редакцию к дате вступления решения в силу).
    """
    version = version or BASE_EDITION
    if day < version:
        return None
    for nid in note_ids:
        note = notes.get(nid)
        if note is None:
            return EffectiveRate(base_rate, "ett_base", version, note_id=nid,
                                 issue=f"примечание {nid}) к ЕТТ не найдено в «Примечаниях к ЕТТ»")
        if note.kind == "void":
            continue
        if note.kind != "rate":
            return EffectiveRate(base_rate, "ett_base", version, note_id=nid,
                                 issue=f"примечание {nid}) к ЕТТ не разобрано: {note.text[:200]}")
        for p in note.periods:
            if p.scope != "all":
                continue
            start = p.valid_from or version
            if start <= day and (p.valid_to is None or day <= p.valid_to):
                return EffectiveRate(p.rate, "ett_note", version, note_id=nid,
                                     valid_from=p.valid_from, valid_to=p.valid_to,
                                     from_decision=p.from_decision)
    return EffectiveRate(base_rate, "ett_base", version)


# ── Хранение и сравнение редакций ────────────────────────────────────────────

def period_to_json(p: NotePeriod) -> dict:
    return {
        "rate": p.rate,
        "valid_from": p.valid_from.isoformat() if p.valid_from else None,
        "valid_to": p.valid_to.isoformat() if p.valid_to else None,
        "from_decision": p.from_decision,
        "scope": p.scope,
    }


def note_from_json(note_id: str, kind: str, periods: list[dict], text: str) -> EttNote:
    def as_date(raw: str | None) -> dt.date | None:
        return dt.date.fromisoformat(raw) if raw else None

    return EttNote(
        note_id=note_id, kind=kind, text=text,
        periods=tuple(
            NotePeriod(
                rate=p["rate"], valid_from=as_date(p.get("valid_from")),
                valid_to=as_date(p.get("valid_to")), from_decision=p.get("from_decision"),
                scope=p.get("scope") or "all",
            )
            for p in periods
        ),
    )


@dataclass(frozen=True)
class StoredRow:
    """Строка ett_rates в виде, нужном для расчёта ставки на дату."""

    base_rate: str
    notes: tuple[str, ...]
    version: dt.date | None


@dataclass(frozen=True)
class EttChange:
    code: str
    change: str                       # rate | added | removed
    before: str | None
    after: str | None
    detail: str | None = None


def diff_editions(
    old: dict[str, StoredRow],
    new: dict[str, StoredRow],
    *,
    old_notes: dict[str, EttNote],
    new_notes: dict[str, EttNote],
    day: dt.date,
) -> list[EttChange]:
    """Что меняется для декларанта на дату day между редакциями (по затронутым кодам)."""
    changes: list[EttChange] = []
    for code in sorted(set(old) | set(new)):
        before_row, after_row = old.get(code), new.get(code)
        if before_row is None and after_row is not None:
            eff = effective_rate(base_rate=after_row.base_rate, note_ids=after_row.notes,
                                 version=after_row.version, notes=new_notes, day=day)
            changes.append(EttChange(code, "added", None, eff.rate if eff else after_row.base_rate))
            continue
        if after_row is None and before_row is not None:
            changes.append(EttChange(code, "removed", before_row.base_rate, None))
            continue
        assert before_row is not None and after_row is not None
        was = effective_rate(base_rate=before_row.base_rate, note_ids=before_row.notes,
                             version=before_row.version, notes=old_notes, day=day)
        now = effective_rate(base_rate=after_row.base_rate, note_ids=after_row.notes,
                             version=after_row.version, notes=new_notes, day=day)
        was_rate = was.rate if was else before_row.base_rate
        now_rate = now.rate if now else after_row.base_rate
        if was_rate != now_rate:
            detail = None
            if now and now.note_id:
                until = f" до {now.valid_to:%d.%m.%Y}" if now.valid_to else ""
                detail = f"примечание {now.note_id}) к ЕТТ{until}"
            changes.append(EttChange(code, "rate", was_rate, now_rate, detail))
    return changes


@dataclass(frozen=True)
class DutyChoice:
    """Ставка пошлины для кода на дату + след для графов/UI + блокирующая проблема."""

    rate: str | None
    trace: dict
    issue: str | None = None


def resolve_duty(
    code: str,
    *,
    fallback_rate: str | None,
    rows: dict[str, StoredRow],
    notes: dict[str, EttNote],
    group_versions: dict[str, dt.date | None],
    day: dt.date,
    fallback_label: str = "снимок TWS",
) -> DutyChoice:
    """Ставка ЕТТ на дату; fallback (tnved_tree.duty_rate) — только если ЕТТ по группе
    не загружен или дата раньше его редакции. Кода нет в действующей редакции —
    issue (needs_input), а не старая ставка."""
    group = code[:2]
    row = rows.get(code)
    if row is None:
        if group in group_versions:
            version = group_versions[group] or BASE_EDITION
            if day >= version:
                return DutyChoice(fallback_rate, {"source": "fallback", "label": fallback_label},
                                  issue=(f"код {code} отсутствует в действующей редакции ЕТТ "
                                         f"(группа {group}, ред. от {version:%d.%m.%Y}) — "
                                         "проверьте код: возможно, он разделён или исключён"))
        return DutyChoice(fallback_rate, {"source": "fallback", "label": fallback_label})
    eff = effective_rate(base_rate=row.base_rate, note_ids=row.notes, version=row.version,
                         notes=notes, day=day)
    if eff is None:
        return DutyChoice(fallback_rate, {
            "source": "fallback", "label": fallback_label,
            "reason": f"дата раньше редакции ЕТТ {row.version:%d.%m.%Y}" if row.version else None,
        })
    trace = {**eff.to_json(), "base_rate": row.base_rate}
    return DutyChoice(eff.rate, trace, issue=eff.issue)
