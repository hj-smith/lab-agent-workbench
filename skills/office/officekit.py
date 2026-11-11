"""Office 工具箱：转 PDF、渲染成图、重算公式、实验室版式。

改写自 DeepSeek Harness 桌面版 office-skills（MIT 许可）。原件用 LibreOffice Kit；这台机器上改用：
  转 PDF / 重算  → 本机 Microsoft Office（COM，不可见，做完关掉），没有时用 LibreOffice（soffice --headless）
  渲染成图       → PPT 用 PowerPoint 直接导出每页 PNG；Word / Excel / PDF 先转 PDF 再用 pdftoppm（poppler）出 PNG
                   没有 pdftoppm 时用 pypdfium2（缺就用 pip 工具装 pypdfium2）

在常驻 python 里：
    from office import officekit as ok
    ok.tools()                                   # 看本机有什么
    ok.to_pdf('报告.docx')                        # → 报告.pdf
    ok.render('汇报.pptx', 'render/汇报', pages=[1, 3])   # → PNG 路径列表，用 files read 或看图工具看
    ok.recalc('数据.xlsx')                        # → 数据-重算.xlsx，并列出公式和算出来的值
命令行（shell 工具）：
    & '<python.exe>' '<本文件>' pdf|render|recalc|tools <输入> [输出]
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
COM = HERE / 'office_com.ps1'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# ---------- 实验室版式约定（和 SKILL.md 里一致） ----------
COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948']
GREY = '#9a9a9a'           # 第 9 条线起用灰色
PAPER = '#fcfcfb'          # 图底色
BODY_FONT = '微软雅黑'      # 正文
HEAD_FONT = '等线'          # 标题、图表、表格可用
LATIN_FONT = 'Arial'

WORD_EXT = {'.doc', '.docx', '.docm', '.rtf', '.odt'}
PPT_EXT = {'.ppt', '.pptx', '.pptm', '.odp'}
XL_EXT = {'.xls', '.xlsx', '.xlsm', '.xlsb', '.ods'}


# ---------- 本机有什么 ----------

def _registered(progid):
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r'\CLSID'))
        return True
    except OSError:
        return False


def _soffice():
    found = shutil.which('soffice') or shutil.which('soffice.com')
    for base in (os.environ.get('ProgramFiles', r'C:\Program Files'), os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')):
        path = Path(base) / 'LibreOffice' / 'program' / 'soffice.com'
        if not found and path.is_file():
            found = str(path)
    return found


def _pdftoppm():
    found = shutil.which('pdftoppm')
    if not found:
        hits = sorted(Path(os.environ.get('LOCALAPPDATA', '')).glob('Microsoft/WinGet/Packages/*Poppler*/*/Library/bin/pdftoppm.exe'))
        found = str(hits[-1]) if hits else None
    return found


def tools():
    """本机能用的转换程序。"""
    return {'word': _registered('Word.Application'), 'powerpoint': _registered('PowerPoint.Application'),
            'excel': _registered('Excel.Application'), 'soffice': _soffice(), 'pdftoppm': _pdftoppm()}


def _kind(path):
    ext = Path(path).suffix.lower()
    return 'word' if ext in WORD_EXT else 'powerpoint' if ext in PPT_EXT else 'excel' if ext in XL_EXT else 'pdf' if ext == '.pdf' else None


# ---------- 跑 Office COM / LibreOffice ----------

def _pids(image):
    text = subprocess.run(['tasklist', '/FI', f'IMAGENAME eq {image}', '/FO', 'CSV', '/NH'], capture_output=True, text=True,
                          errors='replace', creationflags=NO_WINDOW).stdout
    return set(re.findall(r'^"[^"]+","(\d+)"', text, re.M))


def _com(action, src, out, timeout=300, **extra):
    image = {'word': 'WINWORD.EXE', 'powerpoint': 'POWERPNT.EXE', 'excel': 'EXCEL.EXE'}[_kind(src)]
    before = _pids(image)
    args = ['powershell', '-NoProfile', '-NonInteractive', '-File', str(COM), '-Action', action, '-In', str(src), '-Out', str(out)]
    for key, value in extra.items():
        args += [f'-{key}', str(value)]
    try:
        done = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        for pid in _pids(image) - before:     # 卡住时只结束这次新开的、看不见的进程
            subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True, creationflags=NO_WINDOW)
        raise RuntimeError(f'{image} 超过 {timeout} 秒没做完，已结束这次开的进程。可能文件里有要人确认的东西，换 engine="soffice" 试。')
    line = next((item for item in done.stdout.splitlines() if item.startswith('{')), None)
    if done.returncode or not line:
        raise RuntimeError(f'{image} 没做成：{(done.stderr or done.stdout).strip()[-1500:]}')
    return json.loads(line)


def _soffice_run(src, outdir, convert_to, recalc=False, timeout=300):
    exe = _soffice()
    if not exe:
        raise RuntimeError('这台机器没有 Microsoft Office 也没有 LibreOffice。PDF 可以用 reportlab 直接做；重算只能交出没缓存值的公式并说明。')
    profile = Path(tempfile.mkdtemp(prefix='lo-profile-'))
    if recalc:   # 打开 xlsx / ods 时总是重算
        user = profile / 'user'
        user.mkdir(parents=True)
        items = ''.join(f'<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="{name}" oor:op="fuse"><value>0</value></prop></item>'
                        for name in ('OOXMLRecalcMode', 'ODFRecalcMode'))
        (user / 'registrymodifications.xcu').write_text(
            '<?xml version="1.0" encoding="UTF-8"?><oor:items xmlns:oor="http://openoffice.org/2001/registry" '
            f'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">{items}</oor:items>', encoding='utf-8')
    try:
        done = subprocess.run([exe, f'-env:UserInstallation={profile.as_uri()}', '--headless', '--norestore', '--nologo',
                               '--convert-to', convert_to, '--outdir', str(outdir), str(src)],
                              capture_output=True, text=True, errors='replace', timeout=timeout, creationflags=NO_WINDOW)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    made = Path(outdir) / (Path(src).stem + '.' + convert_to.split(':')[0])
    if not made.is_file():
        raise RuntimeError('LibreOffice 没转成：' + (done.stderr or done.stdout).strip()[-1500:])
    return made


def _engine(src, engine):
    if engine != 'auto':
        return engine
    progid = {'word': 'Word.Application', 'powerpoint': 'PowerPoint.Application', 'excel': 'Excel.Application'}.get(_kind(src))
    return 'office' if progid and _registered(progid) else 'soffice'


# ---------- 转 PDF ----------

def to_pdf(src, out=None, engine='auto'):
    """Office 文件 → PDF。engine：auto（先 Microsoft Office，再 LibreOffice）、office、soffice。返回 {pdf, engine, pages, ok}。"""
    src = Path(src).resolve()
    out = Path(out).resolve() if out else src.with_suffix('.pdf')
    out.parent.mkdir(parents=True, exist_ok=True)
    used = _engine(src, engine)
    if used == 'office':
        info = _com('pdf', src, out)
    else:
        with tempfile.TemporaryDirectory() as folder:
            shutil.move(str(_soffice_run(src, folder, 'pdf')), out)
        info = {'engine': 'LibreOffice'}
    head = out.read_bytes()[:5] if out.is_file() else b''
    from pypdf import PdfReader
    pages = len(PdfReader(str(out)).pages) if head == b'%PDF-' else 0
    return {'pdf': str(out), 'engine': info['engine'], 'pages': pages, 'ok': head == b'%PDF-' and pages > 0}


# ---------- 渲染成图 ----------

def _page_list(pages, count):
    if not pages:
        return list(range(1, count + 1))
    if isinstance(pages, str):
        found = []
        for part in pages.split(','):
            start, _, end = part.strip().partition('-')
            found += list(range(int(start), int(end or start) + 1))
        pages = found
    return [page for page in pages if 1 <= page <= count]


def _blank(path):
    from PIL import Image
    with Image.open(path) as image:
        flat = Image.alpha_composite(Image.new('RGBA', image.size, 'white'), image.convert('RGBA')).convert('L')
        low, high = flat.getextrema()
    return high - low < 8


def pdf_to_png(pdf, out_dir, pages=None, dpi=110):
    """PDF → 每页一张 PNG（page-01.png …）。先用 pdftoppm，没有就用 pypdfium2。"""
    from pypdf import PdfReader
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    count = len(PdfReader(str(pdf)).pages)
    want = _page_list(pages, count)
    made = []
    exe = _pdftoppm()
    for page in want:
        target = out_dir / f'page-{page:02d}.png'
        if exe:
            subprocess.run([exe, '-png', '-r', str(dpi), '-f', str(page), '-l', str(page), '-singlefile', str(pdf), str(target.with_suffix(''))],
                           check=True, capture_output=True, creationflags=NO_WINDOW)
        else:
            try:
                import pypdfium2
            except ImportError:
                raise RuntimeError('没有 pdftoppm，也没有 pypdfium2。用 pip 工具装 pypdfium2 再试。')
            document = pypdfium2.PdfDocument(str(pdf))
            document[page - 1].render(scale=dpi / 72).to_pil().save(target)
        made.append(str(target))
    return made, count


def render(src, out_dir=None, pages=None, dpi=110, engine='auto'):
    """docx / pptx / xlsx / pdf → PNG。pages 例 [1,3] 或 "1-3,5"（PPT 是第几页幻灯片；Excel 是导出 PDF 的第几页）。
    返回 {images, pages, engine, blank}：blank 列出全白的图（渲染失败，不要再重试，按 office-render 技能说的收尾）。"""
    src = Path(src).resolve()
    out_dir = Path(out_dir or src.parent / 'render' / src.stem).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    kind, used = _kind(src), _engine(src, engine)
    if kind == 'powerpoint' and used == 'office':
        from pptx import Presentation
        count = len(Presentation(str(src)).slides)
        want = _page_list(pages, count)
        width = int(13.333 * dpi)
        info = _com('slides', src, out_dir, Width=width, Pages=','.join(map(str, want)))
        images, name = [str(out_dir / f'slide-{page:02d}.png') for page in want], info['engine']
    else:
        if kind == 'pdf':
            pdf, name = src, 'PDF'
        else:
            made = to_pdf(src, out_dir / (src.stem + '.pdf'), engine)
            pdf, name = made['pdf'], made['engine']
        images, count = pdf_to_png(pdf, out_dir, pages, dpi)
    return {'images': images, 'pages': count, 'engine': name + (' + pdftoppm' if kind != 'powerpoint' or used != 'office' else ''),
            'blank': [path for path in images if _blank(path)]}


# ---------- 不用眼睛也能做的版面检查 ----------

def page_check(images, edge=0.02):
    """每张渲染图：内容占的范围、离四边多远、是不是贴边（可能被裁掉）、是不是空白。
    页多时先用它挑出可疑页，再用 look 工具去看；模型不能看图时用它代替"看一眼"。"""
    from PIL import Image, ImageChops
    found = []
    for path in images:
        with Image.open(path) as image:
            flat = Image.alpha_composite(Image.new('RGBA', image.size, 'white'), image.convert('RGBA')).convert('RGB')
        corner = flat.getpixel((2, 2))
        box = ImageChops.difference(flat, Image.new('RGB', flat.size, corner)).convert('L').point(lambda v: 255 if v > 24 else 0).getbbox()
        width, height = flat.size
        if not box:
            found.append({'image': Path(path).name, 'blank': True})
            continue
        left, top, right, bottom = box
        margins = {'左': left / width, '上': top / height, '右': (width - right) / width, '下': (height - bottom) / height}
        found.append({'image': Path(path).name, 'blank': False, '内容占宽': round((right - left) / width, 2), '内容占高': round((bottom - top) / height, 2),
                      '边距': {key: round(value, 3) for key, value in margins.items()},
                      '贴边': [key for key, value in margins.items() if value < edge]})
    return found


def pptx_check(path):
    """PPT：每页的形状有没有超出页面、文字估计会不会溢出文本框、字是不是太小、一页字是不是太多。"""
    from pptx import Presentation
    from pptx.util import Emu
    deck = Presentation(str(path))
    width, height = deck.slide_width, deck.slide_height
    report = []
    for number, slide in enumerate(deck.slides, 1):
        notes, chars = [], 0
        for shape in slide.shapes:
            if shape.left is None:
                continue
            if shape.left < 0 or shape.top < 0 or shape.left + shape.width > width or shape.top + shape.height > height:
                notes.append(f'"{shape.name}" 超出页面')
            if shape.has_text_frame and shape.text_frame.text.strip():
                lines_needed, smallest = 0.0, 99
                box_w, box_h = Emu(shape.width).pt, Emu(shape.height).pt
                for paragraph in shape.text_frame.paragraphs:
                    size = next((run.font.size.pt for run in paragraph.runs if run.font.size), 18)
                    smallest = min(smallest, size)
                    text = ''.join(run.text for run in paragraph.runs)
                    chars += len(text)
                    units = sum(1.0 if ord(ch) > 0x2E80 else 0.55 for ch in text)   # 中文一个字宽≈字号，西文约一半
                    lines_needed += max(1, -(-units * size // max(box_w - 14, 1))) * size * 1.2
                if lines_needed > box_h * 1.15 and shape.text_frame.word_wrap is not False:
                    notes.append(f'"{shape.name}" 文字可能溢出（约需 {lines_needed:.0f} pt 高，框 {box_h:.0f} pt）')
                if smallest < 14:
                    notes.append(f'"{shape.name}" 有 {smallest:.0f} pt 的小字（投影建议正文 ≥ 16 pt）')
        if chars > 220:
            notes.append(f'一页 {chars} 字，太多（每页一个意思，图大字少）')
        report.append({'slide': number, 'notes': notes})
    return report


def xlsx_check(path):
    """Excel：哪些单元格的字比列宽长（会被截断或显示 ####）。"""
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter
    book = load_workbook(path, data_only=True)
    found = []
    for sheet in book.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.value is None or (isinstance(cell.value, float) and cell.number_format == 'General'):
                    continue   # "常规"格式的小数 Excel 会自动少显示几位，不算截断
                text = f'{cell.value:.6g}' if isinstance(cell.value, float) else str(cell.value)
                need = sum(2 if ord(ch) > 0x2E80 else 1 for ch in text) + 1
                letter = get_column_letter(cell.column)
                have = sheet.column_dimensions[letter].width if letter in sheet.column_dimensions and sheet.column_dimensions[letter].width else 8.43
                neighbour = sheet.cell(cell.row, cell.column + 1).value
                if need > have and (isinstance(cell.value, (int, float)) or neighbour is not None):
                    found.append(f'{sheet.title}!{cell.coordinate} 需要约 {need} 宽，列宽 {have:.1f}')
    return found


# ---------- 重算公式 ----------

ERRORS = ('#DIV/0!', '#N/A', '#NAME?', '#NULL!', '#NUM!', '#REF!', '#VALUE!', '#SPILL!', '#CALC!')


def formula_report(path, limit=200):
    """列出每个公式和它的缓存值；找出没缓存值的和算出错误的。"""
    from openpyxl import load_workbook
    formulas, values = load_workbook(path, data_only=False), load_workbook(path, data_only=True)
    rows, empty, errors = [], [], []
    for sheet in formulas.worksheets:
        for line in sheet.iter_rows():
            for cell in line:
                if isinstance(cell.value, str) and cell.value.startswith('='):
                    value = values[sheet.title][cell.coordinate].value
                    where = f'{sheet.title}!{cell.coordinate}'
                    rows.append((where, cell.value, value))
                    if value is None:
                        empty.append(where)
                    elif isinstance(value, str) and value in ERRORS:
                        errors.append(f'{where} {value}')
    return {'formulas': len(rows), 'rows': rows[:limit], 'no_value': empty, 'errors': errors}


def recalc(src, out=None, engine='auto'):
    """重算公式，存成新文件（默认 <名字>-重算.xlsx），原件不动。返回 {xlsx, engine, report}。"""
    src = Path(src).resolve()
    out = Path(out).resolve() if out else src.with_name(src.stem + '-重算' + src.suffix)
    out.parent.mkdir(parents=True, exist_ok=True)
    used = _engine(src, engine)
    if used == 'office':
        name = _com('recalc', src, out)['engine']
    else:
        with tempfile.TemporaryDirectory() as folder:
            made = _soffice_run(src, folder, out.suffix.lstrip('.') or 'xlsx', recalc=True)
            shutil.move(str(made), out)
        name = 'LibreOffice'
    return {'xlsx': str(out), 'engine': name, 'report': formula_report(out)}


# ---------- 版式小帮手 ----------

def style_matplotlib():
    """matplotlib 用实验室版式：中文字体、颜色顺序、底色、线宽 2。"""
    import matplotlib as mpl
    from cycler import cycler
    mpl.rcParams.update({
        'font.sans-serif': ['Microsoft YaHei', 'DengXian', 'SimHei', 'Arial'], 'font.family': 'sans-serif', 'axes.unicode_minus': False,
        'axes.prop_cycle': cycler(color=COLORS), 'lines.linewidth': 2, 'figure.facecolor': PAPER, 'axes.facecolor': PAPER,
        'savefig.facecolor': PAPER, 'axes.spines.top': False, 'axes.spines.right': False, 'axes.grid': True,
        'grid.color': '#e6e6e3', 'grid.linewidth': 0.8, 'axes.edgecolor': '#555555', 'font.size': 11, 'figure.dpi': 110, 'savefig.dpi': 200,
        'legend.frameon': False})


def color(index):
    """第 index 条线（从 0 起）的颜色，第 9 条起灰色。"""
    return COLORS[index] if index < len(COLORS) else GREY


def cn_font(run_or_style, name=BODY_FONT, latin=LATIN_FONT):
    """python-docx 的 run 或 style 设中文字体（要同时设 w:eastAsia，只设 font.name 中文不生效）。"""
    from docx.oxml.ns import qn
    run_or_style.font.name = latin
    element = run_or_style.element
    rpr = element.get_or_add_rPr()
    fonts = rpr.find(qn('w:rFonts'))
    if fonts is None:
        fonts = rpr.makeelement(qn('w:rFonts'), {})
        rpr.insert(0, fonts)
    for key, value in (('w:ascii', latin), ('w:hAnsi', latin), ('w:eastAsia', name), ('w:cs', latin)):
        fonts.set(qn(key), value)


def docx_lab_styles(document, size=10.5):
    """新 Word 文档套实验室字体：正文微软雅黑，标题等线，标题黑色。"""
    from docx.shared import Pt, RGBColor
    normal = document.styles['Normal']
    cn_font(normal, BODY_FONT)
    normal.font.size = Pt(size)
    normal.paragraph_format.space_after = Pt(4)
    normal.paragraph_format.line_spacing = 1.25
    for level, points in ((0, 22), (1, 16), (2, 13), (3, 11.5)):
        style = document.styles['Title' if level == 0 else f'Heading {level}']
        cn_font(style, HEAD_FONT)
        style.font.size = Pt(points)
        style.font.bold = True
        style.font.color.rgb = RGBColor(0x1f, 0x1f, 0x1f)
    for name in ('Caption',):
        if name in [item.name for item in document.styles]:
            cn_font(document.styles[name], BODY_FONT)


def three_line_table(table, header_rows=1):
    """python-docx 表格改成三线表：顶线、表头下线 1.5/0.75 磅，底线 1.5 磅，其它不画。"""
    from docx.oxml.ns import qn

    def edges(cell, **sides):
        tcpr = cell._tc.get_or_add_tcPr()
        borders = tcpr.find(qn('w:tcBorders'))
        if borders is None:
            borders = tcpr.makeelement(qn('w:tcBorders'), {})
            tcpr.append(borders)
        for side in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
            size = sides.get(side)
            node = borders.find(qn(f'w:{side}'))
            if node is None:
                node = borders.makeelement(qn(f'w:{side}'), {})
                borders.append(node)
            node.set(qn('w:val'), 'single' if size else 'nil')
            if size:
                node.set(qn('w:sz'), str(size))
                node.set(qn('w:color'), '000000')
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
    from docx.shared import Pt
    table.style = None
    last = len(table.rows) - 1
    for index, row in enumerate(table.rows):
        for cell in row.cells:
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_before = paragraph.paragraph_format.space_after = Pt(2)
                paragraph.paragraph_format.line_spacing = 1.0
                if index < header_rows:
                    for run in paragraph.runs:
                        run.font.bold = True
            sides = {}
            if index == 0:
                sides['top'] = 12
            if index == header_rows - 1:
                sides['bottom'] = 6
            if index == last:
                sides['bottom'] = 12
            edges(cell, **sides)


def pptx_text(shape_or_frame, text, size=18, bold=False, color='1f1f1f', font=BODY_FONT, align=None):
    """python-pptx 文本框写字，中文字体（a:ea）一起设上。返回段落。"""
    from pptx.dml.color import RGBColor
    from pptx.oxml.ns import qn
    from pptx.util import Pt
    frame = getattr(shape_or_frame, 'text_frame', shape_or_frame)
    paragraph = frame.paragraphs[0] if not frame.paragraphs[0].runs else frame.add_paragraph()
    run = paragraph.add_run()
    run.text = text
    run.font.size, run.font.bold, run.font.name = Pt(size), bold, LATIN_FONT
    run.font.color.rgb = RGBColor.from_string(color)
    rpr = run._r.get_or_add_rPr()
    for tag in ('a:ea', 'a:cs'):
        node = rpr.find(qn(tag))
        if node is None:
            node = rpr.makeelement(qn(tag), {})
            rpr.append(node)
        node.set('typeface', font)
    if align is not None:
        paragraph.alignment = align
    return paragraph


if __name__ == '__main__':
    action, *rest = sys.argv[1:] or ['tools']
    result = {'tools': lambda: tools(), 'pdf': lambda: to_pdf(*rest), 'render': lambda: render(*rest),
              'recalc': lambda: recalc(*rest)}[action]()
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
