"""Сборка бизнес-плана: разделы 0–9 из md в docx по образцу БП.docx.

    python business_plan/build.py            → Бизнес_план.docx
    python business_plan/build.py 8          → только раздел 8 (для проверки)

Форматирование взято из БП.docx: Times New Roman 14 pt, полуторный интервал,
абзацный отступ 1,25 см, выравнивание по ширине. Подписи таблиц — над таблицей,
подписи рисунков — под рисунком, стиль Caption, тире «–». Таблицы — Table Grid,
кегль 12, шапка жирная по центру.

Отличия от исходных md, выполняемые при сборке:
  * подпись таблицы в md стоит под таблицей — переносится наверх;
  * блоки «Источники к разделу» изымаются из разделов и собираются в общий
    список в конце документа со сквозной нумерацией; ссылки [N] в тексте
    перенумеровываются соответственно;
  * рисунки и таблицы нумеруются сквозно по всему документу.
"""

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "Бизнес_план.docx"

# порядок разделов и заголовки верхнего уровня.
# 4.1–4.7 — подразделы одного раздела «Анализ рынка», у них Heading 2.
SECTIONS = [
    ("0_резюме.md", "Резюме", 1),
    ("1_общая_характеристика_предприятия.md", "1 Общая характеристика предприятия", 1),
    ("2_описание_продукции.md", "2 Описание продукции", 1),
    ("3_описание_отрасли.md", "3 Описание отрасли", 1),
    ("4.1_анализ_конкурентов.md", "4.1 Анализ конкурентов", 2),
    ("4.2_анализ_потребителей.md", "4.2 Анализ потребителей", 2),
    ("4.3_расчёт_ёмкости_рынка.md", "4.3 Расчёт ёмкости рынка", 2),
    ("4.4_план_продвижения.md", "4.4 План продвижения и бюджет", 2),
    ("4.5_организация_сбыта.md", "4.5 Организация сбыта", 2),
    ("4.6_ценовая_политика.md", "4.6 Ценовая политика", 2),
    ("4.7_swot_анализ.md", "4.7 SWOT-анализ", 2),
    ("5_организационный_план.md", "5 Организационный план", 1),
    ("6_производственный_план.md", "6 Производственный план", 1),
    ("7_финансовый_план.md", "7 Финансовый план", 1),
    ("8_инвестиционный_план.md", "8 Инвестиционный план", 1),
    ("9_анализ_рисков.md", "9 Анализ рисков", 1),
]

# перед подразделами 4.x ставится заголовок раздела «Анализ рынка»
GROUP_HEADINGS = {"4.1_анализ_конкурентов.md": "4 Анализ рынка"}

FONT = "Times New Roman"
SIZE = Pt(14)
SIZE_TABLE = Pt(12)
FIRST_LINE = Cm(1.25)
LINE_SPACING = 1.5
IMG_WIDTH = Cm(15.0)
BLACK = RGBColor(0, 0, 0)

SOURCES_HEADING = "Источники к разделу"


# --------------------------------------------------------------------------
# низкоуровневые помощники
# --------------------------------------------------------------------------
def style_run(run, *, bold=False, italic=False, size=SIZE):
    run.font.name = FONT
    run.font.size = size
    run.bold = bold
    run.italic = italic
    run.font.color.rgb = BLACK          # стили Heading/Caption в шаблоне синие
    # для кириллицы Word смотрит на eastAsia/cs, иначе шрифт подменяется
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), FONT)


def add_seq_field(p, name):
    """Поле { SEQ name \\* ARABIC } — Word пересчитывает номер сам.

    Без поля номер был бы обычным текстом: вставка таблицы в готовый документ
    сбивала бы всю последующую нумерацию. Значение подставляет Word при
    открытии либо по F9, поэтому в поле кладётся заглушка «1».
    """
    def el(tag, **attrs):
        e = OxmlElement(tag)
        for k, v in attrs.items():
            e.set(qn(k), v)
        return e

    r = p.add_run()
    r._element.append(el("w:fldChar", **{"w:fldCharType": "begin"}))
    style_run(r)

    r = p.add_run()
    instr = el("w:instrText", **{"xml:space": "preserve"})
    instr.text = f" SEQ {name} \\* ARABIC "
    r._element.append(instr)
    style_run(r)

    r = p.add_run()
    r._element.append(el("w:fldChar", **{"w:fldCharType": "separate"}))
    style_run(r)

    style_run(p.add_run("1"))          # заглушка: заменится при пересчёте

    r = p.add_run()
    r._element.append(el("w:fldChar", **{"w:fldCharType": "end"}))
    style_run(r)


def add_ref_field(p, bookmark, *, size=SIZE):
    """Поле { REF bookmark \\h } — перекрёстная ссылка на номер объекта.

    Ссылки «в таблице 3» в тексте иначе остались бы статическими и разошлись
    бы с номерами при вставке таблицы в готовый документ.
    """
    def el(tag, **attrs):
        e = OxmlElement(tag)
        for k, v in attrs.items():
            e.set(qn(k), v)
        return e

    r = p.add_run()
    r._element.append(el("w:fldChar", **{"w:fldCharType": "begin"}))
    style_run(r, size=size)

    r = p.add_run()
    instr = el("w:instrText", **{"xml:space": "preserve"})
    instr.text = f" REF {bookmark} \\h "
    r._element.append(instr)
    style_run(r, size=size)

    r = p.add_run()
    r._element.append(el("w:fldChar", **{"w:fldCharType": "separate"}))
    style_run(r, size=size)

    style_run(p.add_run("1"), size=size)       # заглушка

    r = p.add_run()
    r._element.append(el("w:fldChar", **{"w:fldCharType": "end"}))
    style_run(r, size=size)


def wrap_bookmark(p, name, bid):
    """Обернуть содержимое абзаца закладкой (цель перекрёстных ссылок)."""
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), str(bid))
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), str(bid))
    p._element.insert(0, start)
    p._element.append(end)


def style_paragraph(p, *, first_line=FIRST_LINE, align=WD_ALIGN_PARAGRAPH.JUSTIFY,
                    line_spacing=LINE_SPACING):
    pf = p.paragraph_format
    pf.first_line_indent = first_line
    pf.left_indent = Cm(0)
    pf.right_indent = Cm(0)
    pf.line_spacing = line_spacing
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    p.alignment = align


# --------------------------------------------------------------------------
# разбор markdown
# --------------------------------------------------------------------------
INLINE = re.compile(r"(\*\*.+?\*\*|\*.+?\*|`.+?`)")


def add_inline(p, text, *, bold=False, italic=False, size=SIZE):
    """Текст с **жирным**, *курсивом* и `моноширинным` → runs."""
    text = text.replace("—", "–")          # в документе — среднее тире
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            add_inline(p, part[2:-2], bold=True, italic=italic, size=size)
        elif part.startswith("*") and part.endswith("*"):
            add_inline(p, part[1:-1], bold=bold, italic=True, size=size)
        elif part.startswith("`") and part.endswith("`"):
            add_inline(p, part[1:-1], bold=bold, italic=italic, size=size)
        else:
            style_run(p.add_run(part), bold=bold, italic=italic, size=size)


def collect_items(lines, i, marker, strip_marker):
    """Пункты списка, начиная со строки i. Перенесённые строки приклеиваются
    к своему пункту: в md пункт может занимать несколько строк.

    Пункты бывают разделены пустой строкой; список на ней не заканчивается,
    иначе каждый пункт стал бы отдельным списком с нумерацией заново.
    """
    items = []
    pat = re.compile(rf"^{marker}")
    while i < len(lines) and pat.match(lines[i].strip()):
        items.append(strip_marker(lines[i].strip()))
        i += 1
        # продолжение пункта — строка с отступом, не начинающая новый пункт
        while i < len(lines) and lines[i].strip() \
                and lines[i].startswith(" ") and not pat.match(lines[i].strip()):
            items[-1] += " " + lines[i].strip()
            i += 1
        # пустая строка перед следующим пунктом — часть того же списка
        j = i
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and pat.match(lines[j].strip()):
            i = j
    return items, i


def parse_md(path: Path):
    """md → (блоки, источники).

    Блок: ('h2'|'para'|'bullet'|'num'|'table'|'image'|'caption', данные).
    Источники — список строк из блока «Источники к разделу»; в блоки не попадает.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks, sources, i = [], [], 0
    in_sources = False
    while i < len(lines):
        ln = lines[i].rstrip()
        if not ln.strip():
            i += 1
            continue
        # заголовок файла (# ...) — подставляется из SECTIONS
        if ln.startswith("# "):
            i += 1
            continue
        if ln.startswith("## "):
            title = ln[3:].strip()
            if title == SOURCES_HEADING:
                in_sources = True
                i += 1
                continue
            in_sources = False
            blocks.append(("h2", title))
            i += 1
            continue
        if in_sources:
            # нумерованный источник может занимать несколько строк
            if re.match(r"\d+\.\s", ln.strip()):
                src = re.sub(r"^\d+\.\s+", "", ln.strip())
                i += 1
                while i < len(lines) and lines[i].strip() \
                        and not re.match(r"\d+\.\s", lines[i].strip()) \
                        and not lines[i].startswith("#"):
                    src += " " + lines[i].strip()
                    i += 1
                sources.append(src)
            else:
                i += 1
            continue
        # картинка ![...](path)
        m = re.match(r"!\[[^\]]*\]\(([^)]+)\)", ln)
        if m:
            blocks.append(("image", m.group(1)))
            i += 1
            continue
        # подпись: *Рисунок N — ...* или *Таблица N — ...*; может переноситься
        if re.match(r"\*(Рисунок|Таблица)\s", ln):
            cap = [ln]
            i += 1
            while not cap[-1].rstrip().endswith("*") and i < len(lines) \
                    and lines[i].strip():
                cap.append(lines[i].strip())
                i += 1
            blocks.append(("caption", " ".join(cap).strip("*")))
            continue
        # таблица
        if ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            blocks.append(("table", rows))
            continue
        if ln.startswith("- "):
            items, i = collect_items(lines, i, r"-\s", lambda s: s[2:])
            blocks.append(("bullet", items))
            continue
        if re.match(r"\d+\.\s", ln):
            items, i = collect_items(lines, i, r"\d+\.\s",
                                     lambda s: re.sub(r"^\d+\.\s+", "", s))
            blocks.append(("num", items))
            continue
        # абзац: md переносится по ширине, поэтому строки до пустой — один абзац
        para = [ln.strip()]
        i += 1
        while i < len(lines) and lines[i].strip() \
                and not lines[i].startswith(("#", "|", "- ", "!")) \
                and not re.match(r"\d+\.\s", lines[i].strip()) \
                and not re.match(r"\*(Рисунок|Таблица)\s", lines[i].strip()):
            para.append(lines[i].strip())
            i += 1
        blocks.append(("para", " ".join(para)))

    # подпись таблицы в md стоит под таблицей — поднять над ней
    for n in range(1, len(blocks)):
        kind, data = blocks[n]
        if kind == "caption" and data.startswith("Таблица") \
                and blocks[n - 1][0] == "table":
            blocks[n - 1], blocks[n] = blocks[n], blocks[n - 1]

    return blocks, sources


# --------------------------------------------------------------------------
# сборка документа
# --------------------------------------------------------------------------
class Builder:
    def __init__(self, doc):
        self.doc = doc
        self.fig_no = 0
        self.tbl_no = 0
        self.sources = []            # сквозной список источников
        self.src_offset = 0          # смещение номеров текущего раздела
        self.bm_id = 0               # счётчик id закладок
        self.bookmarks = {}          # (раздел, вид, номер в разделе) → закладка
        self.sect_idx = 0            # номер обрабатываемого раздела
        self.loc_fig = 0             # номер рисунка внутри раздела
        self.loc_tbl = 0             # номер таблицы внутри раздела
        self.first_heading = True    # перед первым разделом разрыв не нужен

    # --- ссылки [N] на источники ---
    def renumber_refs(self, text):
        """[3] → [3 + смещение]; [5], [7] тоже. Диапазоны в md не встречаются."""
        if not self.src_offset:
            return text
        return re.sub(r"\[(\d+)\]",
                      lambda m: f"[{int(m.group(1)) + self.src_offset}]", text)

    def prescan(self, blocks, sect_idx):
        """Заранее зарегистрировать номера подписей раздела.

        Ссылка «в таблице 3» может стоять раньше самой подписи, поэтому имена
        закладок должны быть известны до отрисовки текста.
        """
        fig = tbl = 0
        for kind, data in blocks:
            if kind == "image":
                fig += 1
            elif kind == "caption":
                if data.strip().startswith("Таблица"):
                    tbl += 1
                    self.bookmarks[(sect_idx, "Таблица", tbl)] = f"_Tbl_{sect_idx}_{tbl}"
                else:
                    self.bookmarks[(sect_idx, "Рисунок", fig)] = f"_Fig_{sect_idx}_{fig}"

    def render(self, blocks, heading, level):
        self._heading(heading, level)
        for kind, data in blocks:
            getattr(self, f"_{kind}")(data)

    # --- виды блоков ---
    def _heading(self, text, level):
        p = self.doc.add_paragraph(style=f"Heading {level}")
        style_run(p.add_run(text), bold=True)
        style_paragraph(p)
        p.paragraph_format.keep_with_next = True
        # раздел первого уровня — с новой страницы; подразделы 4.x идут подряд
        if level == 1 and not self.first_heading:
            p.paragraph_format.page_break_before = True
        self.first_heading = False

    def _h2(self, text):
        p = self.doc.add_paragraph()
        style_run(p.add_run(text), bold=True)
        style_paragraph(p)
        p.paragraph_format.keep_with_next = True

    # «таблице 3», «таблица 3», «рисунке 1» — ссылка на объект своего раздела
    XREF = re.compile(r"\b(таблиц|рисун)(е|а|ах|ке|ок|ки)\s+(\d+)", re.I)

    def add_text(self, p, text, *, size=SIZE):
        """Текст абзаца: ссылки на таблицы и рисунки — полями REF.

        Без полей номера в тексте разошлись бы с номерами подписей, как только
        в готовый документ вставят таблицу.
        """
        text = self.renumber_refs(text)
        pos = 0
        for m in self.XREF.finditer(text):
            bm = self.bookmarks.get((self.sect_idx,
                                     "Таблица" if m.group(1).lower().startswith("таблиц")
                                     else "Рисунок",
                                     int(m.group(3))))
            if not bm:                      # ссылка на объект другого раздела
                continue
            add_inline(p, text[pos:m.start()], size=size)
            style_run(p.add_run(m.group(1) + m.group(2) + " "), size=size)
            add_ref_field(p, bm, size=size)
            pos = m.end()
        add_inline(p, text[pos:], size=size)

    def _para(self, text):
        p = self.doc.add_paragraph()
        self.add_text(p, text)
        style_paragraph(p)

    def _bullet(self, items):
        for it in items:
            p = self.doc.add_paragraph(style="List Paragraph")
            style_run(p.add_run("– "))
            self.add_text(p, it)
            style_paragraph(p)

    def _num(self, items):
        for n, it in enumerate(items, 1):
            p = self.doc.add_paragraph(style="List Paragraph")
            style_run(p.add_run(f"{n}. "))
            self.add_text(p, it)
            style_paragraph(p)

    def _image(self, rel_path):
        img = (ROOT / rel_path).resolve()
        self.fig_no += 1
        self.loc_fig += 1
        p = self.doc.add_paragraph()
        style_paragraph(p, first_line=Cm(0), align=WD_ALIGN_PARAGRAPH.CENTER)
        p.paragraph_format.keep_with_next = True
        if img.exists():
            p.add_run().add_picture(str(img), width=IMG_WIDTH)
        else:
            style_run(p.add_run(f"[рисунок: {rel_path}]"), italic=True)

    def _caption(self, text):
        """«Таблица 3 — Название» → «Таблица {SEQ Таблица} – Название».

        Номер из md отбрасывается: его подставляет поле SEQ, и при вставке
        таблицы вручную нумерация остаётся верной.
        """
        text = text.strip()
        m = re.match(r"(Рисунок|Таблица)\s+\d+\s*[—–-]\s*(.*)", text, re.S)
        if not m:                                   # подпись без номера
            kind, tail = ("Рисунок" if text.startswith("Рисунок") else "Таблица"), text
        else:
            kind, tail = m.group(1), m.group(2)
        if kind == "Рисунок":
            align = WD_ALIGN_PARAGRAPH.CENTER      # счётчик ведёт _image
        else:
            self.tbl_no += 1
            self.loc_tbl += 1
            align = WD_ALIGN_PARAGRAPH.LEFT

        p = self.doc.add_paragraph(style="Caption")
        style_run(p.add_run(f"{kind} "))
        # закладка охватывает только номер: REF подставит его, а не всю подпись
        self.bm_id += 1
        local_no = self.loc_fig if kind == "Рисунок" else self.loc_tbl
        bm = f"_{'Fig' if kind == 'Рисунок' else 'Tbl'}_{self.sect_idx}_{local_no}"
        self.bookmarks[(self.sect_idx, kind, local_no)] = bm
        start = OxmlElement("w:bookmarkStart")
        start.set(qn("w:id"), str(self.bm_id))
        start.set(qn("w:name"), bm)
        p._element.append(start)
        add_seq_field(p, kind)
        end = OxmlElement("w:bookmarkEnd")
        end.set(qn("w:id"), str(self.bm_id))
        p._element.append(end)
        style_run(p.add_run(" – "))
        self.add_text(p, tail)
        style_paragraph(p, first_line=Cm(0), align=align, line_spacing=1.0)
        p.paragraph_format.keep_with_next = (align == WD_ALIGN_PARAGRAPH.LEFT)
        p.paragraph_format.space_before = Pt(6)
        if kind == "Рисунок":
            # отбивка перед следующим абзацем: подпись не должна липнуть к тексту
            sp = self.doc.add_paragraph()
            style_paragraph(sp, first_line=Cm(0))

    @staticmethod
    def _fit_columns(t, rows):
        """Ширина колонки пропорциональна длине её содержимого.

        Равные колонки расходуют место впустую: в таблицах бизнес-плана первый
        столбец обычно текстовый, остальные — числа. Доля ограничена снизу,
        чтобы узкая колонка не сжалась до переноса числа по цифрам.
        """
        ncols = len(rows[0])
        weights = []
        for c in range(ncols):
            # длина самой длинной ячейки, но с поправкой на перенос текста:
            # длинный текст переносится, поэтому вес растёт медленнее длины
            longest = max(len(r[c]) for r in rows if c < len(r))
            weights.append(longest ** 0.6)
        total = sum(weights)
        avail = Cm(16.5)                      # ширина полосы набора
        widths = [max(Cm(1.6), Cm(16.5 * w / total)) for w in weights]
        # после нижней отсечки сумма могла превысить полосу — ужать пропорционально
        over = sum(w.cm for w in widths) / avail.cm
        if over > 1:
            widths = [Cm(w.cm / over) for w in widths]
        for c, w in enumerate(widths):
            for row in t.rows:                # Word берёт ширину из ячеек
                row.cells[c].width = w
            t.columns[c].width = w

    def _table(self, rows):
        if not rows:
            return
        t = self.doc.add_table(rows=len(rows), cols=len(rows[0]))
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.autofit = False
        # шапка повторяется при переносе таблицы на следующую страницу
        tr_pr = t.rows[0]._element.get_or_add_trPr()
        tr_pr.append(OxmlElement("w:tblHeader"))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                if c >= len(t.columns):
                    continue
                cell = t.cell(r, c)
                cell.text = ""
                p = cell.paragraphs[0]
                self.add_text(p, val, size=SIZE_TABLE)
                # шапка по центру, тело по левому краю; числа в первом
                # столбце — текст, поэтому выравнивание единое
                align = WD_ALIGN_PARAGRAPH.CENTER if r == 0 else WD_ALIGN_PARAGRAPH.LEFT
                style_paragraph(p, first_line=Cm(0), align=align, line_spacing=1.0)
                for run in p.runs:
                    run.font.size = SIZE_TABLE
                    if r == 0:
                        run.bold = True
        self._fit_columns(t, rows)
        # пустой абзац после таблицы, иначе соседние таблицы слипаются
        p = self.doc.add_paragraph()
        style_paragraph(p, first_line=Cm(0))
        p.paragraph_format.space_after = Pt(6)


# --------------------------------------------------------------------------
# первая страница — сведения о грантополучателе в том виде, в каком их требует
# Фонд; титульный лист с названием проекта Фонду не нужен
TITLE_LINES = [
    "Получатель гранта: Гричанов Игорь",
    "Адрес: 630120, Новосибирская область, г. Новосибирск, "
    "ул, Титова, 255/1, кв. 61",
    "Телефон: +7 (996) 637-00-29",
    "Договор: 1105ГССС27/106900 от 17.10.2025",
    "Название стартап-проекта: Создание цифровой платформы предиктивной "
    "персонализации клиентского обслуживания в общепите",
]


def make_title_page(doc):
    for line in TITLE_LINES:
        p = doc.add_paragraph()
        style_run(p.add_run(line), bold=True)
        style_paragraph(p)


def make_toc_page(doc):
    """Страница 2 — содержание: поле TOC, Word собирает его по F9.

    Поле берёт заголовки уровней 1–2 (\\o "1-2"), номера страниц — по правому
    краю с заполнителем, гиперссылками (\\h \\z \\u).
    """
    p = doc.add_paragraph()
    style_run(p.add_run("Содержание"), bold=True)
    style_paragraph(p, first_line=Cm(0), align=WD_ALIGN_PARAGRAPH.CENTER)
    p.paragraph_format.space_after = Pt(12)

    p = doc.add_paragraph()
    style_paragraph(p, first_line=Cm(0))
    r = p.add_run()
    # без w:dirty: иначе Word при открытии спрашивает про обновление полей,
    # «ссылающихся на другие файлы». Поле обновляется по F9 вместе с прочими.
    r._element.append(_fld("begin"))
    style_run(r)

    r = p.add_run()
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = r' TOC \o "1-2" \h \z \u '
    r._element.append(instr)
    style_run(r)

    r = p.add_run()
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    r._element.append(sep)
    style_run(r)

    style_run(p.add_run("Обновите поле: Ctrl+A, затем F9"), italic=True)

    r = p.add_run()
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    r._element.append(end)
    style_run(r)


def add_page_numbers(section, start):
    """Номер страницы справа внизу, начиная с номера start.

    Титул и содержание входят в свою секцию без колонтитула, поэтому счёт
    основной секции начинается с 3.
    """
    sect_pr = section._sectPr
    pg = sect_pr.find(qn("w:pgNumType"))
    if pg is None:
        pg = OxmlElement("w:pgNumType")
        sect_pr.append(pg)
    pg.set(qn("w:start"), str(start))

    section.footer.is_linked_to_previous = False
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    r = p.add_run()
    r._element.append(_fld("begin"))
    style_run(r)
    r = p.add_run()
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE   \\* MERGEFORMAT "
    r._element.append(instr)
    style_run(r)
    r = p.add_run()
    r._element.append(_fld("separate"))
    style_run(r)
    style_run(p.add_run(str(start)))
    r = p.add_run()
    r._element.append(_fld("end"))
    style_run(r)


def _fld(kind):
    e = OxmlElement("w:fldChar")
    e.set(qn("w:fldCharType"), kind)
    return e


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    sections = SECTIONS
    if only:
        sections = [s for s in SECTIONS if s[0].startswith(only)]
        if not sections:
            raise SystemExit(f"Раздел «{only}» не найден")

    doc = Document()
    # поля страницы как в БП.docx
    s = doc.sections[0]
    s.page_width, s.page_height = Cm(21.0), Cm(29.7)      # A4
    s.left_margin, s.right_margin = Cm(3.0), Cm(1.5)
    s.top_margin, s.bottom_margin = Cm(2.0), Cm(2.0)

    if not only:
        make_title_page(doc)
        doc.add_section(WD_SECTION.NEW_PAGE)     # стр. 2 — содержание
        make_toc_page(doc)
        body = doc.add_section(WD_SECTION.NEW_PAGE)   # стр. 3 — начало текста
        for s2 in (doc.sections[1], body):
            s2.page_width, s2.page_height = Cm(21.0), Cm(29.7)
            s2.left_margin, s2.right_margin = Cm(3.0), Cm(1.5)
            s2.top_margin, s2.bottom_margin = Cm(2.0), Cm(2.0)
        # нумерация с первой страницы: поле ставится в первой секции,
        # последующие наследуют колонтитул и продолжают счёт
        add_page_numbers(doc.sections[0], start=1)
        for s2 in (doc.sections[1], body):
            s2.footer.is_linked_to_previous = True

    builder = Builder(doc)
    # предварительный проход: имена закладок должны быть известны до отрисовки
    parsed = [(name, heading, level) + parse_md(ROOT / name)
              for name, heading, level in sections]
    for idx, (name, _h, _l, blocks, _s) in enumerate(parsed):
        builder.prescan(blocks, idx)

    for idx, (name, heading, level, blocks, sources) in enumerate(parsed):
        if name in GROUP_HEADINGS and not only:
            builder._heading(GROUP_HEADINGS[name], 1)
        builder.sect_idx = idx
        builder.loc_fig = builder.loc_tbl = 0
        builder.src_offset = len(builder.sources)
        builder.render(blocks, heading, level)
        builder.sources.extend(sources)
        print(f"  {name}: {len(blocks)} блоков, источников {len(sources)}")

    # общий список источников со сквозной нумерацией
    if builder.sources:
        builder._heading("Список используемых источников", 1)
        for n, src in enumerate(builder.sources, 1):
            p = doc.add_paragraph(style="List Paragraph")
            style_run(p.add_run(f"{n}. "))
            add_inline(p, src)
            style_paragraph(p)

    doc.save(OUTPUT)
    print(f"\nРисунков {builder.fig_no}, таблиц {builder.tbl_no}, "
          f"источников {len(builder.sources)}")
    print(f"Готово: {OUTPUT}")


if __name__ == "__main__":
    main()
