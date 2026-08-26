"""Сборка Отчёта: подстановка разделов 1.1–1.5 в шаблон Отчет.docx.

Заменяет абзацы-плейсхолдеры «На данном этапе …» содержимым md-файлов,
приводит заголовки разделов к единому уровню и чинит нумерацию страниц.

    python otchet/build.py            → Отчет_заполненный.docx

Форматирование: Times New Roman 14 pt, полуторный интервал, абзацный отступ
1,25 см, выравнивание по ширине. Рисунки нумеруются сквозно.
"""

import re
import shutil
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT.parent / "Отчет.docx"
OUTPUT = ROOT.parent / "Отчет_заполненный.docx"

SECTIONS = ["1.1_сайт.md", "1.2_база_данных.md", "1.3_архитектура.md",
            "1.4_интеграция_iiko.md", "1.5_платформа.md"]

FONT = "Times New Roman"
SIZE = Pt(14)
FIRST_LINE = Cm(1.25)
LINE_SPACING = 1.5
IMG_WIDTH = Cm(16.0)


# --------------------------------------------------------------------------
# низкоуровневые помощники
# --------------------------------------------------------------------------
def style_run(run, *, bold=False, italic=False, size=SIZE):
    run.font.name = FONT
    run.font.size = size
    run.bold = bold
    run.italic = italic
    # для кириллицы Word смотрит на eastAsia/cs, иначе шрифт подменяется
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), FONT)


def style_paragraph(p, *, first_line=FIRST_LINE, align=WD_ALIGN_PARAGRAPH.JUSTIFY):
    pf = p.paragraph_format
    pf.first_line_indent = first_line
    pf.left_indent = Cm(0)
    pf.right_indent = Cm(0)
    pf.line_spacing = LINE_SPACING
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    p.alignment = align


def insert_after(ref_el, new_el):
    ref_el.addnext(new_el)
    return new_el


def new_paragraph_after(ref_p, doc):
    """Пустой абзац сразу после ref_p (Normal, без наследования стиля)."""
    p = doc.add_paragraph()
    ref_p._element.addnext(p._element)
    return p


# --------------------------------------------------------------------------
# разбор markdown
# --------------------------------------------------------------------------
INLINE = re.compile(r"(\*\*.+?\*\*|\*.+?\*|`.+?`)")


def add_inline(p, text, *, bold=False, italic=False):
    """Текст с **жирным**, *курсивом* и `моноширинным` → runs.

    Разбор рекурсивный: разметка вкладывается друг в друга (**`order_items`**),
    и без рекурсии внутренние обратные кавычки попадали бы в текст как есть.
    """
    text = text.replace("—", "–")          # в отчёте — среднее тире
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            add_inline(p, part[2:-2], bold=True, italic=italic)
        elif part.startswith("*") and part.endswith("*"):
            add_inline(p, part[1:-1], bold=bold, italic=True)
        elif part.startswith("`") and part.endswith("`"):
            # обратные кавычки — разметка markdown, в документе не нужны
            add_inline(p, part[1:-1], bold=bold, italic=italic)
        else:
            style_run(p.add_run(part), bold=bold, italic=italic)


def parse_md(path: Path):
    """md → список блоков: ('h2'|'para'|'bullet'|'num'|'table'|'image'|'caption', данные)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks, i = [], 0
    while i < len(lines):
        ln = lines[i].rstrip()
        if not ln.strip():
            i += 1
            continue
        # заголовок раздела файла (# ...) пропускаем — он уже есть в шаблоне
        if ln.startswith("# "):
            i += 1
            continue
        if ln.startswith("## "):
            blocks.append(("h2", ln[3:].strip()))
            i += 1
            continue
        # картинка ![...](path)
        m = re.match(r"!\[[^\]]*\]\(([^)]+)\)", ln)
        if m:
            blocks.append(("image", m.group(1)))
            i += 1
            continue
        # подпись к рисунку: *Рисунок N — ...*
        if ln.startswith("*Рисунок") and ln.endswith("*"):
            blocks.append(("caption", ln.strip("*")))
            i += 1
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
        # маркированный список
        if ln.startswith("- "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(lines[i].strip()[2:])
                i += 1
            blocks.append(("bullet", items))
            continue
        # нумерованный список
        if re.match(r"\d+\.\s", ln):
            items = []
            while i < len(lines) and re.match(r"\d+\.\s", lines[i].strip()):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i].strip()))
                i += 1
            blocks.append(("num", items))
            continue
        blocks.append(("para", ln))
        i += 1
    return blocks


# --------------------------------------------------------------------------
# вставка блоков в документ
# --------------------------------------------------------------------------
class Builder:
    def __init__(self, doc):
        self.doc = doc
        self.fig_no = 0          # сквозная нумерация рисунков
        self.fig_map = {}        # путь → номер (подпись берёт номер отсюда)

    def render(self, blocks, anchor):
        """Вставляет блоки после anchor (абзац-плейсхолдер). Возвращает последний элемент."""
        cur = anchor
        for kind, data in blocks:
            cur = getattr(self, f"_{kind}")(data, cur)
        return cur

    # --- отдельные виды блоков ---
    def _para(self, text, cur):
        p = self.doc.add_paragraph()
        cur.addnext(p._element)
        add_inline(p, text)
        style_paragraph(p)
        return p._element

    def _h2(self, text, cur):
        p = self.doc.add_paragraph()
        cur.addnext(p._element)
        style_run(p.add_run(text), bold=True)
        style_paragraph(p, first_line=Cm(0), align=WD_ALIGN_PARAGRAPH.LEFT)
        p.paragraph_format.space_before = Pt(12)
        p.paragraph_format.space_after = Pt(6)
        p.paragraph_format.keep_with_next = True
        return p._element

    def _bullet(self, items, cur):
        for it in items:
            p = self.doc.add_paragraph()
            cur.addnext(p._element)
            style_run(p.add_run("– "))
            add_inline(p, it)
            style_paragraph(p)
            cur = p._element
        return cur

    def _num(self, items, cur):
        for n, it in enumerate(items, 1):
            p = self.doc.add_paragraph()
            cur.addnext(p._element)
            style_run(p.add_run(f"{n}. "))
            add_inline(p, it)
            style_paragraph(p)
            cur = p._element
        return cur

    def _image(self, rel_path, cur):
        img = (ROOT / rel_path).resolve()
        self.fig_no += 1
        self.fig_map[rel_path] = self.fig_no

        p = self.doc.add_paragraph()
        cur.addnext(p._element)
        style_paragraph(p, first_line=Cm(0), align=WD_ALIGN_PARAGRAPH.CENTER)
        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.keep_with_next = True
        if img.exists():
            p.add_run().add_picture(str(img), width=IMG_WIDTH)
        else:                                   # не нашли файл — оставим метку
            style_run(p.add_run(f"[рисунок: {rel_path}]"), italic=True)
        return p._element

    def _caption(self, text, cur):
        # перенумеровать подпись согласно сквозному счётчику
        text = re.sub(r"^Рисунок\s+\d+", f"Рисунок {self.fig_no}", text.strip())
        p = self.doc.add_paragraph()
        cur.addnext(p._element)
        style_run(p.add_run(text.replace("—", "–")))
        style_paragraph(p, first_line=Cm(0), align=WD_ALIGN_PARAGRAPH.CENTER)
        p.paragraph_format.space_after = Pt(12)
        return p._element

    def _table(self, rows, cur):
        if not rows:
            return cur
        t = self.doc.add_table(rows=len(rows), cols=len(rows[0]))
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                if c >= len(t.columns):
                    continue
                cell = t.cell(r, c)
                cell.text = ""
                p = cell.paragraphs[0]
                add_inline(p, val)
                style_paragraph(p, first_line=Cm(0), align=WD_ALIGN_PARAGRAPH.LEFT)
                p.paragraph_format.line_spacing = 1.0
                for run in p.runs:
                    run.font.size = Pt(12)
                    if r == 0:
                        run.bold = True
        cur.addnext(t._element)
        # после таблицы нужен пустой абзац, иначе следующая таблица «слипнется»
        p = self.doc.add_paragraph()
        t._element.addnext(p._element)
        style_paragraph(p, first_line=Cm(0))
        p.paragraph_format.space_after = Pt(6)
        return p._element


# --------------------------------------------------------------------------
# нумерация страниц: start="2" оставить только в первой секции
# --------------------------------------------------------------------------
def normalize_headings(doc):
    """Заголовки 1.1–1.5 → Heading2 и разрыв страницы перед каждым.

    В шаблоне разметка была разнородной: 1.1–1.2 имели стиль Heading2, а
    1.3–1.5 — Heading1, из-за чего в оглавлении они попадали на разные уровни.
    Разбиение на страницы тоже было непоследовательным: перед 1.2 стоял разрыв
    страницы, перед 1.3 и 1.4 — разрывы разделов, перед 1.5 не было ничего.
    """
    restyled = broken = 0
    for p in doc.paragraphs:
        if not re.match(r"^1\.\d\s", p.text.strip()):
            continue
        if p.style.name != "Heading 2":
            p.style = doc.styles["Heading 2"]
            restyled += 1

        prev = p._element.getprevious()
        # разрыв раздела уже разделяет страницы — второй разрыв дал бы пустую
        has_sect = prev is not None and prev.find(qn("w:pPr")) is not None \
            and prev.find(qn("w:pPr")).find(qn("w:sectPr")) is not None
        has_break = bool(p._element.findall(f".//{qn('w:br')}[@{qn('w:type')}='page']"))
        if not has_sect and not has_break and p.text.strip() != "1.1 Создание сайта стартап-проекта":
            pr = p._element.get_or_add_pPr()
            pb = pr.find(qn("w:pageBreakBefore"))
            if pb is None:
                pr.insert(0, OxmlElement("w:pageBreakBefore"))
                broken += 1
    return restyled, broken


def fix_page_numbering(doc):
    body = doc.element.body
    sect_prs = body.findall(qn("w:sectPr")) + [
        p.find(qn("w:pPr")).find(qn("w:sectPr"))
        for p in body.findall(qn("w:p"))
        if p.find(qn("w:pPr")) is not None
        and p.find(qn("w:pPr")).find(qn("w:sectPr")) is not None
    ]
    # порядок следования в документе
    sect_prs = [s for s in sect_prs if s is not None]
    sect_prs.sort(key=lambda e: list(body.iter()).index(e))

    fixed = 0
    for idx, sect in enumerate(sect_prs):
        pg = sect.find(qn("w:pgNumType"))
        if idx == 0:
            continue                       # первая секция: нумерация со 2-й страницы
        if pg is not None:
            sect.remove(pg)                # остальные — продолжают счёт
            fixed += 1
    return len(sect_prs), fixed


# --------------------------------------------------------------------------
def main():
    shutil.copy(TEMPLATE, OUTPUT)
    doc = Document(OUTPUT)

    # плейсхолдеры «На данном этапе …» в порядке следования
    holders = [p for p in doc.paragraphs if p.text.strip().startswith("На данном этапе")]
    if len(holders) != len(SECTIONS):
        raise SystemExit(f"Ожидалось {len(SECTIONS)} плейсхолдеров, найдено {len(holders)}")

    builder = Builder(doc)
    for holder, name in zip(holders, SECTIONS):
        blocks = parse_md(ROOT / name)
        builder.render(blocks, holder._element)
        holder._element.getparent().remove(holder._element)   # убрать плейсхолдер
        print(f"  {name}: {len(blocks)} блоков")

    restyled, broken = normalize_headings(doc)
    print(f"\nЗаголовки: приведено к Heading2 — {restyled}, добавлено разрывов страниц — {broken}")

    total, fixed = fix_page_numbering(doc)
    print(f"\nНумерация страниц: секций {total}, снят принудительный старт в {fixed}")
    print(f"Рисунков вставлено: {builder.fig_no}")

    doc.save(OUTPUT)
    print(f"\nГотово: {OUTPUT}")


if __name__ == "__main__":
    main()
