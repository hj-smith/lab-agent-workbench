"""NK Studio 纯分析核心。来自 v2.2.0 工作簿引擎；不导入或创建 Excel 工作簿。
仅 xlsx 输入解析按需使用 openpyxl，数学算法保留原实现。"""

from __future__ import annotations

import csv,json,math,re,unicodedata

from dataclasses import dataclass,field

from datetime import datetime

from pathlib import Path

from typing import Any,Iterable,Optional

from zipfile import ZipFile,BadZipFile

from xml.etree import ElementTree as ET

import numpy as np

import pandas as pd

def _scipy_available():
    # Warm desktop sessions need no SciPy until a feature/UV analysis is used.
    # Cache the original functions and availability flag, preserving the public API.
    if 'SCIPY_AVAILABLE' not in globals():
        try:
            from scipy.signal import find_peaks, peak_widths, savgol_filter
        except ImportError:
            globals()['SCIPY_AVAILABLE'] = False
        else:
            globals().update(SCIPY_AVAILABLE=True, find_peaks=find_peaks,
                             peak_widths=peak_widths, savgol_filter=savgol_filter)
    return globals()['SCIPY_AVAILABLE']


def __getattr__(name):
    if name == '__all__':
        # Historical compatibility modules use star imports, including the
        # optional SciPy names only when that dependency can actually load.
        _scipy_available()
        return [key for key in globals() if not key.startswith('_')]
    if name in {'SCIPY_AVAILABLE', 'find_peaks', 'peak_widths', 'savgol_filter'}:
        _scipy_available()
        if name in globals():
            return globals()[name]
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')

VERSION="3.0.0"

WAVE_ALIASES = {
    "wavelength",
    "wavelengthnm",
    "wavelengthum",
    "wavelengthμm",
    "wavelengthµm",
    "wave",
    "lambda",
    "λ",
    "波长",
    "波长nm",
    "波长um",
    "波长μm",
    "波长µm",
}
N_ALIASES = {"n", "refractiveindex", "折射率"}
K_ALIASES = {"k", "extinctioncoefficient", "消光系数"}

THEME = {
    "navy": "16324F",
    "teal": "0F6B78",
    "cyan": "DDF3F4",
    "light": "F3F6F8",
    "input": "FFF4CC",
    "formula": "E8F0FE",
    "green": "E2F0D9",
    "yellow": "FFF2CC",
    "red": "FCE4D6",
    "blue": "DDEBF7",
    "muted": "607D8B",
    "grid": "D9E2E8",
    "white": "FFFFFF",
    "dark": "203040",
}

SAMPLE_COLORS = [
    "0F6B78",
    "2F75B5",
    "70AD47",
    "ED7D31",
    "A64D79",
    "5B9BD5",
    "8064A2",
    "C55A11",
    "548235",
    "4472C4",
]


@dataclass
class Config:
    """集中管理所有分析参数，避免魔法数字散落。"""

    grid_mode: str = "共同波长范围"
    custom_start_nm: Optional[float] = None
    custom_end_nm: Optional[float] = None
    grid_step_nm: float = 1.0
    interpolation_method: str = "线性插值"
    allow_extrapolation: bool = False
    reference_sample: str = ""
    enable_smoothing: bool = False
    smoothing_window: int = 11
    peak_prominence_fraction: float = 0.03
    max_features_per_signal: int = 20
    k_zero_threshold: float = 1e-8
    target_wavelengths_nm: list[float] = field(
        default_factory=lambda: [193, 248, 365, 405, 436, 532, 633, 780, 940]
    )
    bands: dict[str, tuple[float, float]] = field(
        default_factory=lambda: {
            "UV": (190.0, 400.0),
            "可见光": (400.0, 780.0),
            "NIR": (780.0, 1000.0),
        }
    )
    jump_window: int = 11
    jump_multiplier: float = 8.0
    large_gap_multiplier: float = 5.0
    dashboard_max_samples: int = 12
    dashboard_group_count: int = 12
    series_per_chart: int = 12
    plot_palette: str = '现代学术'
    plot_line_mode: str = '彩色实线'
    plot_line_width: float = 1.4
    plot_size: str = '大图'
    plot_tick_count: int = 8
    plot_grid: bool = True
    solva_dashed: bool = True
    plot_series_styles: dict = field(default_factory=dict)
    export_focus_report: bool = True
    export_dpi: int = 600
    export_formats: list[str] = field(default_factory=lambda: ['pdf','png','svg'])
    export_presets: list[dict] = field(default_factory=list)
    dashboard_sources: list[str] = field(default_factory=list)
    dashboard_summary_sources: list[str] = field(default_factory=list)
    dashboard_groups: list[str] = field(default_factory=list)
    dashboard_repeatability_summary_groups: list[str] = field(default_factory=list)
    dashboard_sigma_groups: list[str] = field(default_factory=list)
    anomaly_z_threshold: float = 3.5
    anomaly_fraction: float = 0.05
    anomaly_min_group: int = 3
    anomaly_min_points: int = 20
    anomaly_min_delta_n: float = 0.0
    anomaly_min_delta_k: float = 0.0
    unspecified_unit_assumption: str = "nm（暂存，需人工确认）"


@dataclass
class SampleData:
    """一个成功导入样品的全部数据与审计信息。"""

    sample_id: str
    file_name: str
    file_path: str
    encoding: str
    delimiter: str
    header_row: int
    wavelength_header: str
    wavelength_unit: str
    unit_status: str
    raw: pd.DataFrame
    clean: pd.DataFrame
    qc: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    simulated: bool = False
    included: bool = True
    dashboard: bool = True
    is_reference: bool = False
    repeatability_included: bool = True


@dataclass(frozen=True)
class ReferenceSource:
    """统一表示单独样品或重复性组均值参考数据源。"""

    source_id: str
    source_type: str
    group_name: str = ""

    @property
    def sample_id(self) -> str:
        """兼容既有写入逻辑；该值实际为统一数据源 ID。"""

        return self.source_id


@dataclass
class ImportRecord:
    """导入日志中的单文件记录。"""

    file_name: str
    file_path: str
    status: str
    import_time: str
    encoding: str = ""
    delimiter: str = ""
    header_row: Optional[int] = None
    detected_headers: str = ""
    wavelength_unit: str = ""
    total_rows: int = 0
    valid_rows: int = 0
    abnormal_rows: int = 0
    message: str = ""


def normalize_header(value: Any) -> str:
    """标准化表头，用于严格别名匹配，不进行模糊猜测。"""

    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"[\s_\-\(\)\[\]/\\]+", "", text)


def infer_repeatability_group(file_name: str) -> str:
    """按文件名自动识别重复测量组。

    文件名任意位置出现独立的 ``-1``～``-6`` 时移除该重复编号；其余字符
    保持不变。例如 ``A-1-UV.txt`` 与 ``A-6-UV.txt`` 都归为 ``A-UV``。
    """

    stem = Path(file_name).stem.strip()
    # 连字符本身已经给出了编号左边界，因此不能再限制连字符前一个字符
    # 必须为非数字；否则 PA-T16-1 会因连字符左侧是“6”而漏分组。
    # 仅限制编号后面不能继续跟数字，以免把 -10、-25 等误当成 -1、-2。
    group = re.sub(r"-[1-6](?!\d)", "", stem)
    group = re.sub(r"[-_.\s]{2,}", "-", group).strip("-_. ")
    return group or stem


def _legacy_repeatability_group(file_name: str) -> str:
    """返回 1.5.0 及更早版本的自动分组值，仅用于旧工作簿迁移。"""

    stem = Path(file_name).stem.strip()
    group = re.sub(r"(?<!\d)-[1-6](?!\d)", "", stem)
    group = re.sub(r"[-_.\s]{2,}", "-", group).strip("-_. ")
    return group or stem


def classify_header(value: Any) -> Optional[str]:
    """将表头严格分类为 wavelength / n / k。"""

    key = normalize_header(value)
    if key in WAVE_ALIASES:
        return "wavelength"
    if key in N_ALIASES:
        return "n"
    if key in K_ALIASES:
        return "k"
    return None


def detect_columns(headers: list[Any]) -> dict[str, int]:
    """严格识别三列；任一列缺失或重复都报错。"""

    found: dict[str, list[int]] = {"wavelength": [], "n": [], "k": []}
    for idx, header in enumerate(headers):
        category = classify_header(header)
        if category:
            found[category].append(idx)
    problems = [name for name, idxs in found.items() if len(idxs) != 1]
    if problems:
        details = ", ".join(f"{name}={found[name]}" for name in problems)
        raise ValueError(f"无法可靠识别三列（要求每列恰好一个）：{details}")
    return {name: idxs[0] for name, idxs in found.items()}


def detect_unit(header: Any) -> tuple[str, float, str]:
    """从波长表头判断单位；不依据数值范围静默推断。"""

    raw = unicodedata.normalize("NFKC", str(header or "")).casefold()
    compact = re.sub(r"\s+", "", raw)
    if "nm" in compact or "nanomet" in compact:
        return "nm", 1.0, "明确"
    if any(token in compact for token in ("μm", "µm", "um", "micromet")):
        return "μm", 1000.0, "明确，已换算为 nm"
    return "未明确", 1.0, "需要人工确认（按 nm 暂存）"


def sanitize_sample_id(value: str) -> str:
    """生成适合作为样品编号的文本；工作表名不按样品拆分。"""

    text = unicodedata.normalize("NFKC", str(value)).strip()
    text = re.sub(r"[\x00-\x1f]+", "_", text)
    text = re.sub(r"\s+", "_", text)
    return text[:80] or "Sample"


def make_unique_ids(samples: list[SampleData]) -> None:
    """对重复样品编号追加 _2、_3 等后缀。"""

    used: set[str] = set()
    for sample in samples:
        base = sanitize_sample_id(sample.sample_id)
        candidate, count = base, 1
        while candidate.casefold() in used:
            count += 1; candidate = f'{base}_{count}'
        used.add(candidate.casefold()); sample.sample_id = candidate


def scan_input_files(input_dir: Path) -> list[Path]:
    """扫描支持的输入文件，稳定排序。"""

    supported = {".txt", ".csv", ".dat", ".xlsx", ".xlsm"}
    return sorted(
        [
            path
            for path in input_dir.iterdir()
            if path.is_file() and path.suffix.casefold() in supported
            and not path.name.startswith(('~$','.nk-stage-')) and not is_analysis_workbook(path)
        ],
        key=lambda p: p.name.casefold(),
    )


def decode_text_file(path: Path) -> tuple[str, str]:
    """依次尝试常见编码，全部失败时返回明确错误。"""

    raw = path.read_bytes()
    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            errors.append(f"{encoding}: {exc}")
    raise ValueError("无法使用 UTF-8/UTF-8-SIG/GBK/GB18030 解码；" + " | ".join(errors))


def split_line(line: str, delimiter: str) -> list[str]:
    """按候选分隔符解析一行；CSV 类分隔符保留引号语义。"""

    if delimiter == "连续空格":
        return re.split(r"\s+", line.strip()) if line.strip() else []
    char = {"制表符": "\t", "逗号": ",", "分号": ";"}[delimiter]
    return next(csv.reader([line], delimiter=char))


def detect_text_layout(text: str) -> tuple[str, int, list[str], dict[str, int]]:
    """在前 40 行中识别表头行与分隔符。"""

    lines = text.splitlines()
    candidates: list[tuple[int, int, str, list[str], dict[str, int]]] = []
    for line_idx, line in enumerate(lines[:40]):
        if not line.strip():
            continue
        for priority, delimiter in enumerate(("制表符", "逗号", "分号", "连续空格")):
            fields = split_line(line, delimiter)
            if delimiter == '连续空格':
                fields = re.findall(r'Wavelength\s*\((?:nm|μm|µm|um)\)|Refractive\s+Index|Extinction\s+Coefficient|\S+',line.strip(),flags=re.I)
            while fields and not str(fields[-1]).strip():
                fields.pop()
            if len(fields) < 3:
                continue
            try:
                columns = detect_columns(fields)
            except ValueError:
                continue
            candidates.append((line_idx, priority, delimiter, fields, columns))
    if not candidates:
        raise ValueError("前 40 行未发现可可靠识别的 Wavelength/n/k 表头")
    candidates.sort(key=lambda item: (item[0], item[1], len(item[3])))
    line_idx, _, delimiter, fields, columns = candidates[0]
    return delimiter, line_idx, fields, columns


def build_raw_dataframe(
    records: list[tuple[int, list[Any]]],
    headers: list[Any],
    columns: dict[str, int],
    unit_factor: float,
    unit_status: str,
) -> pd.DataFrame:
    """把原始记录转换为长表，保留异常行与原始行号。"""

    rows: list[dict[str, Any]] = []
    for original_row, fields in records:
        padded = list(fields) + [""] * max(0, len(headers) - len(fields))
        if all(not str(value).strip() for value in padded):
            continue
        extra_note = ""
        if len(fields) > len(headers) and any(str(v).strip() for v in fields[len(headers) :]):
            extra_note = "存在未映射的额外字段"
        values = {
            "wavelength": padded[columns["wavelength"]],
            "n": padded[columns["n"]],
            "k": padded[columns["k"]],
        }
        numeric: dict[str, float] = {}
        notes: list[str] = []
        for key, value in values.items():
            text = str(value).strip()
            if text == "":
                numeric[key] = math.nan
                notes.append(f"{key} 缺失")
            else:
                try:
                    numeric[key] = float(text)
                    if not np.isfinite(numeric[key]):
                        numeric[key] = math.nan
                        notes.append(f'{key} 非有限数值: {text[:40]}')
                except (TypeError, ValueError):
                    numeric[key] = math.nan
                    notes.append(f"{key} 非数值: {text[:40]}")
        wavelength_nm = numeric["wavelength"] * unit_factor if np.isfinite(numeric["wavelength"]) else math.nan
        if np.isfinite(wavelength_nm) and wavelength_nm <= 0:
            notes.append('波长≤0；仅保留原始记录，不用于计算')
        if "需要人工确认" in unit_status:
            notes.append(unit_status)
        if extra_note:
            notes.append(extra_note)
        rows.append(
            {
                "Original_Row": int(original_row),
                "Wavelength_nm": wavelength_nm,
                "n": numeric["n"],
                "k": numeric["k"],
                "Data_Status": "正常" if not notes else "异常/待确认",
                "Import_Note": "；".join(notes),
            }
        )
    return pd.DataFrame(
        rows,
        columns=["Original_Row", "Wavelength_nm", "n", "k", "Data_Status", "Import_Note"],
    )


def parse_text_file(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    """解析 txt/csv/dat，返回原始长表和识别元数据。"""

    text, encoding = decode_text_file(path)
    delimiter, header_idx, headers, columns = detect_text_layout(text)
    unit, factor, unit_status = detect_unit(headers[columns["wavelength"]])
    records: list[tuple[int, list[Any]]] = []
    for idx, line in enumerate(text.splitlines()[header_idx + 1 :], start=header_idx + 2):
        if not line.strip():
            continue
        fields = split_line(line, delimiter)
        while len(fields) > len(headers) and not str(fields[-1]).strip():
            fields.pop()
        records.append((idx, fields))
    raw = build_raw_dataframe(records, headers, columns, factor, unit_status)
    return raw, {
        "encoding": encoding,
        "delimiter": delimiter,
        "header_row": header_idx + 1,
        "headers": headers,
        "wavelength_header": str(headers[columns["wavelength"]]),
        "wavelength_unit": unit,
        "unit_status": unit_status,
    }


def parse_excel_file(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    """解析 xlsx/xlsm；在前 40 行中严格寻找表头。"""

    frame = pd.read_excel(path, header=None, dtype=object, engine="openpyxl")
    frame = frame.dropna(axis=1, how="all").dropna(axis=0, how="all")
    header_position: Optional[int] = None
    headers: list[Any] = []
    columns: dict[str, int] = {}
    for row_pos in range(min(40, len(frame))):
        values = frame.iloc[row_pos].tolist()
        try:
            detected = detect_columns(values)
        except ValueError:
            continue
        header_position = row_pos
        headers = values
        columns = detected
        break
    if header_position is None:
        raise ValueError("Excel 前 40 行未发现可可靠识别的 Wavelength/n/k 表头")
    unit, factor, unit_status = detect_unit(headers[columns["wavelength"]])
    records: list[tuple[int, list[Any]]] = []
    for row_pos in range(header_position + 1, len(frame)):
        values = frame.iloc[row_pos].tolist()
        values = ["" if pd.isna(v) else v for v in values]
        records.append((int(frame.index[row_pos]) + 1, values))
    raw = build_raw_dataframe(records, headers, columns, factor, unit_status)
    return raw, {
        "encoding": "Excel 工作簿",
        "delimiter": "单元格列",
        "header_row": int(frame.index[header_position]) + 1,
        "headers": headers,
        "wavelength_header": str(headers[columns["wavelength"]]),
        "wavelength_unit": unit,
        "unit_status": unit_status,
    }


def robust_jump_count(values: np.ndarray, cfg: Config, signal: str = 'n') -> int:
    """透明突跳检测：相邻差值 > 局部差值中位数 × 倍数。"""

    arr = np.asarray(values, dtype=float)
    if arr.size < 4:
        return 0
    diffs = np.abs(np.diff(arr))
    series = pd.Series(diffs)
    window = max(3, int(cfg.jump_window))
    if window % 2 == 0:
        window += 1
    local = series.rolling(window=window, center=True, min_periods=2).median().to_numpy()
    global_median = float(np.nanmedian(diffs)) if np.any(np.isfinite(diffs)) else 0.0
    baseline = np.where(np.isfinite(local) & (local > 0), local, global_median)
    absolute_floor = max(cfg.anomaly_min_delta_k, cfg.k_zero_threshold) if signal == 'k' else cfg.anomaly_min_delta_n
    floor = max(global_median * cfg.jump_multiplier, absolute_floor, np.finfo(float).eps)
    flags = (diffs > baseline * cfg.jump_multiplier) & (diffs > floor)
    return int(np.count_nonzero(flags))


def build_quality(raw: pd.DataFrame, clean: pd.DataFrame, info: dict[str, Any], cfg: Config) -> dict[str, Any]:
    """计算逐样品质量检查结果。"""

    valid_w = raw["Wavelength_nm"].dropna().to_numpy(dtype=float)
    original_sorted = bool(valid_w.size < 2 or np.all(np.diff(valid_w) >= 0))
    duplicate_count = int(clean.attrs.get("duplicate_count", 0))
    missing_count = int(
        raw[["Wavelength_nm", "n", "k"]].isna().any(axis=1).sum()
    )
    nonnumeric_count = int(raw["Import_Note"].astype(str).str.contains("非数值", regex=False).sum())
    n_nonpositive = int((raw["n"] <= 0).fillna(False).sum())
    k_negative = int((raw["k"] < 0).fillna(False).sum())
    if len(clean) >= 2:
        spacing = np.diff(clean["Wavelength_nm"].to_numpy(dtype=float))
        median_spacing = float(np.median(spacing))
        large_gap_count = int(np.count_nonzero(spacing > median_spacing * cfg.large_gap_multiplier))
    else:
        median_spacing = math.nan
        large_gap_count = 0
    jump_n = robust_jump_count(clean["n"].to_numpy(dtype=float), cfg) if len(clean) else 0
    jump_k = robust_jump_count(clean["k"].to_numpy(dtype=float), cfg, 'k') if len(clean) else 0
    issues: list[str] = []
    if duplicate_count:
        issues.append(f"重复波长 {duplicate_count} 个（分析时取均值）")
    if missing_count:
        issues.append(f"缺失/不可用记录 {missing_count} 行")
    if nonnumeric_count:
        issues.append(f"非数值记录 {nonnumeric_count} 行")
    if n_nonpositive:
        issues.append(f"n≤0 共 {n_nonpositive} 行")
    if k_negative:
        issues.append(f"k<0 共 {k_negative} 行")
    if not original_sorted:
        issues.append("原始顺序非递增（分析副本已排序）")
    if jump_n or jump_k:
        issues.append(f"突跳候选 n={jump_n}, k={jump_k}")
    if large_gap_count:
        issues.append(f"异常大间隔 {large_gap_count} 处")
    if not len(clean):
        overall = "错误"
    elif "需要人工确认" in info["unit_status"]:
        overall = "待人工确认"
    elif issues:
        overall = "警告"
    else:
        overall = "通过"
    return {
        "header_ok": True,
        "unit_status": info["unit_status"],
        "original_sorted": original_sorted,
        "duplicate_count": duplicate_count,
        "missing_count": missing_count,
        "nonnumeric_count": nonnumeric_count,
        "n_nonpositive_count": n_nonpositive,
        "k_negative_count": k_negative,
        "jump_n_count": jump_n,
        "jump_k_count": jump_k,
        "large_gap_count": large_gap_count,
        "median_spacing": median_spacing,
        "overall": overall,
        "message": "；".join(issues) if issues else "未发现预设规则下的异常",
    }


def parse_sample_file(path: Path, cfg: Config) -> tuple[SampleData, ImportRecord]:
    """解析单个文件；调用方负责隔离异常。"""

    imported_at = datetime.now().astimezone().isoformat(timespec="seconds")
    if path.suffix.casefold() in {".xlsx", ".xlsm"}:
        raw, info = parse_excel_file(path)
    else:
        raw, info = parse_text_file(path)
    valid_mask = np.isfinite(raw[["Wavelength_nm", "n", "k"]]).all(axis=1) & (raw['Wavelength_nm'] > 0)
    valid = raw.loc[valid_mask, ["Wavelength_nm", "n", "k"]].copy()
    if valid.empty:
        raise ValueError("三列转换后没有任何完整数值记录")
    valid = valid.sort_values("Wavelength_nm", kind="mergesort")
    duplicate_count = int(valid.duplicated("Wavelength_nm", keep=False).sum())
    clean = valid.groupby("Wavelength_nm", as_index=False, sort=True)[["n", "k"]].mean()
    clean.attrs["duplicate_count"] = duplicate_count
    qc = build_quality(raw, clean, info, cfg)
    sample = SampleData(
        sample_id=path.stem,
        file_name=path.name,
        file_path=str(path.resolve()),
        encoding=info["encoding"],
        delimiter=info["delimiter"],
        header_row=info["header_row"],
        wavelength_header=info["wavelength_header"],
        wavelength_unit=info["wavelength_unit"],
        unit_status=info["unit_status"],
        raw=raw,
        clean=clean,
        qc=qc,
        metadata={
            "样品名称": path.stem,
            "配方或材料类别": "",
            "批次号": "",
            "分组": infer_repeatability_group(path.name),
            "测试日期": "",
            "膜厚_nm": "",
            "基材": "",
            "测试仪器": "",
            "测试模型": "",
            "操作人员": "",
            "备注": "",
        },
    )
    log = ImportRecord(
        file_name=path.name,
        file_path=str(path.resolve()),
        status="成功",
        import_time=imported_at,
        encoding=info["encoding"],
        delimiter=info["delimiter"],
        header_row=info["header_row"],
        detected_headers=" | ".join(str(v) for v in info["headers"] if str(v).strip()),
        wavelength_unit=info["wavelength_unit"],
        total_rows=len(raw),
        valid_rows=len(clean),
        abnormal_rows=int((raw["Data_Status"] != "正常").sum()),
        message=qc["message"],
    )
    return sample, log



def _optional_float(value: Any) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(value)
        return number if np.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _positive_float(value: Any, default: float) -> float:
    number = _optional_float(value)
    return number if number is not None and number > 0 else default



def apply_sample_overrides(
    samples: list[SampleData],
    overrides: dict[str, dict[str, Any]],
) -> None:
    """应用便携软件提供的样品配置，不依赖先生成并编辑 Excel。

    覆盖以原始文件名为键；未列出的文件继续使用导入时的透明默认值。
    """

    for sample in samples:
        item = overrides.get(sample.file_name)
        if not item:
            continue
        sample.included = bool(item.get("included", sample.included))
        sample.dashboard = bool(item.get("dashboard", sample.dashboard))
        sample.repeatability_included = bool(
            item.get("repeatability_included", sample.repeatability_included)
        )
        requested_id = str(item.get("sample_id", "")).strip()
        if requested_id:
            sample.sample_id = sanitize_sample_id(requested_id)
        metadata = item.get("metadata", {})
        if isinstance(metadata, dict):
            for key in (
                "样品名称",
                "配方或材料类别",
                "批次号",
                "分组",
                "测试日期",
                "膜厚_nm",
                "基材",
                "测试仪器",
                "测试模型",
                "操作人员",
                "备注",
            ):
                if key in metadata and metadata[key] is not None:
                    value = metadata[key]
                    if key == "分组" and not str(value).strip():
                        continue
                    sample.metadata[key] = value


def select_reference(samples: list[SampleData], cfg: Config) -> SampleData:
    """确定单样品后备参考；组均值参考会在重复性曲线生成后解析。"""

    included = [s for s in samples if s.included]
    if not included:
        raise ValueError("没有任何“是否纳入分析=是”的成功样品")
    requested = str(cfg.reference_sample or "").strip()
    exact = [s for s in included if s.sample_id == requested]
    if exact:
        reference = exact[0]
    else:
        selected = [s for s in included if s.is_reference]
        reference = selected[0] if selected else next(
            (s for s in included if not s.simulated),
            included[0],
        )
    for sample in samples:
        sample.is_reference = bool(exact and sample is reference)
    if not requested.startswith("组均值｜"):
        cfg.reference_sample = reference.sample_id
    return reference


def validate_config(cfg: Config) -> None:
    """软件界面、JSON 和命令行共用的配置检查；不静默改写错误选项。"""
    from .anomaly_detection import validate_parameters
    validate_parameters(cfg)
    if cfg.grid_mode not in {'共同波长范围','所有样品波长范围并集','用户自定义'}:
        raise ValueError(f'不支持的网格模式：{cfg.grid_mode}')
    if cfg.interpolation_method != '线性插值' or cfg.allow_extrapolation:
        raise ValueError('当前版本只支持范围内线性插值，不支持外推')
    from .plot_options import validate_plot_options
    validate_plot_options(cfg)
    if not cfg.target_wavelengths_nm or any(not np.isfinite(v) or v <= 0 for v in cfg.target_wavelengths_nm):
        raise ValueError('至少设置一个正的、有限的目标波长')
    if len(set(cfg.target_wavelengths_nm)) != len(cfg.target_wavelengths_nm):
        raise ValueError('目标波长不能重复')
    if set(cfg.bands) != {'UV','可见光','NIR'} or any(len(b)!=2 or not all(np.isfinite(v) for v in b) or not 0<b[0]<b[1] for b in cfg.bands.values()):
        raise ValueError('UV、可见光、NIR 波段必须有正数起点和更大的终点')
    if cfg.grid_mode=='用户自定义' and (cfg.custom_start_nm is None or cfg.custom_end_nm is None or not all(np.isfinite(v) for v in (cfg.custom_start_nm,cfg.custom_end_nm)) or not 0<cfg.custom_start_nm<cfg.custom_end_nm):
        raise ValueError('自定义波长起止必须为正数且终点大于起点')
    if cfg.enable_smoothing and (cfg.smoothing_window<5 or cfg.smoothing_window%2!=1):
        raise ValueError('平滑窗口必须为不小于 5 的奇数')
    if not np.isfinite(cfg.peak_prominence_fraction) or cfg.peak_prominence_fraction<=0 or cfg.max_features_per_signal<1:
        raise ValueError('峰显著性参数和最大特征数必须为正数')
    if cfg.k_zero_threshold<0 or cfg.jump_multiplier<=0 or cfg.large_gap_multiplier<=0:
        raise ValueError('k 阈值不能为负，突跳/大间隔倍数必须为正')
    for name in ('smoothing_window','max_features_per_signal','jump_window'):
        value=getattr(cfg,name)
        if not np.isfinite(value) or value<1 or int(value)!=value:
            raise ValueError(f'{name} 必须是正整数')


def generate_common_grid(samples: list[SampleData], cfg: Config) -> np.ndarray:
    """生成严格递增公共网格；并集模式也不意味着允许外推。"""

    included = [s for s in samples if s.included]
    mins = [float(s.clean["Wavelength_nm"].min()) for s in included]
    maxs = [float(s.clean["Wavelength_nm"].max()) for s in included]
    if cfg.grid_mode == "所有样品波长范围并集":
        start, end = min(mins), max(maxs)
    elif cfg.grid_mode == "用户自定义":
        if cfg.custom_start_nm is None or cfg.custom_end_nm is None:
            raise ValueError("用户自定义网格必须填写起始与终止波长")
        start, end = cfg.custom_start_nm, cfg.custom_end_nm
    else:
        cfg.grid_mode = "共同波长范围"
        start, end = max(mins), min(maxs)
    if not start < end:
        raise ValueError(f"公共波长范围为空：start={start}, end={end}")
    step = float(cfg.grid_step_nm)
    first = math.ceil((start - 1e-10) / step) * step
    last = math.floor((end + 1e-10) / step) * step
    if (last-first)/step+1 > 100000:
        raise ValueError('公共网格超过 100000 点，请增大步长')
    grid = np.arange(first, last + step * 0.25, step, dtype=float)
    grid = grid[grid <= last + 1e-9]
    if grid.size < 2 or not np.all(np.diff(grid) > 0):
        raise ValueError("公共波长网格生成失败或点数不足")
    return np.round(grid, 9)


def interpolate_sample(sample: SampleData, grid: np.ndarray) -> dict[str, np.ndarray]:
    """线性插值且绝不外推；精确命中原始波长时优先原始值。"""

    x = sample.clean["Wavelength_nm"].to_numpy(dtype=float)
    result: dict[str, np.ndarray] = {}
    in_range = (grid >= x[0] - 1e-10) & (grid <= x[-1] + 1e-10)
    source = np.full(grid.size, "范围外", dtype=object)
    exact = np.zeros(grid.size, dtype=bool)
    idx = np.searchsorted(x, grid, side="left")
    idx_clipped = np.clip(idx, 0, len(x) - 1)
    exact |= np.isclose(x[idx_clipped], grid, rtol=0, atol=1e-9)
    previous = np.clip(idx - 1, 0, len(x) - 1)
    exact_prev = np.isclose(x[previous], grid, rtol=0, atol=1e-9)
    exact |= exact_prev
    source[in_range] = "插值值"
    source[in_range & exact] = "原始值"
    exact_indices = np.flatnonzero(in_range & exact)
    if np.all(np.isfinite(x)) and np.all(x[1:] >= x[:-1]):
        # Find the FIRST close point, including duplicates and several close neighbours.
        # Searching grid-atol can round across the tolerance boundary. Instead binary
        # search with the original isclose predicate (actual subtraction, rtol=0).
        targets = grid[exact_indices]
        left = np.zeros(len(targets), dtype=np.intp)
        right = np.full(len(targets), len(x), dtype=np.intp)
        while np.any(left < right):
            active = np.flatnonzero(left < right)
            middle = left[active] + (right[active] - left[active]) // 2
            values = x[middle]
            before = (values < targets[active]) & ~np.isclose(
                values, targets[active], rtol=0, atol=1e-9)
            left[active] = np.where(before, middle + 1, left[active])
            right[active] = np.where(before, right[active], middle)
        match_indices = np.minimum(left, len(x) - 1)
        found = (left < len(x)) & np.isclose(x[match_indices], targets, rtol=0, atol=1e-9)
        exact_indices, match_indices = exact_indices[found], match_indices[found]
    else:
        # Preserve legacy behaviour for non-finite/unsorted direct callers as well.
        pairs = []
        for gi in exact_indices:
            matches = np.flatnonzero(np.isclose(x, grid[gi], rtol=0, atol=1e-9))
            if matches.size:
                pairs.append((gi, matches[0]))
        exact_indices = np.asarray([gi for gi, _ in pairs], dtype=np.intp)
        match_indices = np.asarray([xi for _, xi in pairs], dtype=np.intp)
    for signal in ("n", "k"):
        y = sample.clean[signal].to_numpy(dtype=float)
        values = np.full(grid.size, np.nan, dtype=float)
        values[in_range] = np.interp(grid[in_range], x, y)
        values[exact_indices] = y[match_indices]
        result[signal] = values
    result["source"] = source
    result["in_range"] = in_range
    return result


def derive_optical(
    wavelength_nm: np.ndarray, n: np.ndarray, k: np.ndarray, k_threshold: float
) -> dict[str, np.ndarray]:
    """计算 ε1、ε2、α、R 和穿透深度；不计算光学带隙。"""

    wavelength_nm = np.asarray(wavelength_nm, dtype=float)
    n = np.asarray(n, dtype=float)
    k = np.asarray(k, dtype=float)
    valid = np.isfinite(wavelength_nm) & (wavelength_nm > 0) & np.isfinite(n) & np.isfinite(k)
    eps1 = np.full_like(wavelength_nm, np.nan)
    eps2 = np.full_like(wavelength_nm, np.nan)
    alpha = np.full_like(wavelength_nm, np.nan)
    reflectance = np.full_like(wavelength_nm, np.nan)
    depth = np.full_like(wavelength_nm, np.nan)
    eps1[valid] = n[valid] ** 2 - k[valid] ** 2
    eps2[valid] = 2.0 * n[valid] * k[valid]
    alpha[valid] = 4.0 * np.pi * k[valid] / (wavelength_nm[valid] * 1e-7)
    denominator=(n+1.0)**2+k**2
    reflection_valid=valid & (denominator>0)
    reflectance[reflection_valid]=((n[reflection_valid]-1.0)**2+k[reflection_valid]**2)/denominator[reflection_valid]
    depth_valid = valid & (k > k_threshold)
    depth[depth_valid] = wavelength_nm[depth_valid] / (4.0 * np.pi * k[depth_valid])
    return {"epsilon1": eps1, "epsilon2": eps2, "alpha": alpha, "R": reflectance, "delta": depth}


def value_at_target(sample: SampleData, target: float) -> dict[str, Any]:
    """提取目标波长；范围内线性插值、范围外留空。"""

    x = sample.clean["Wavelength_nm"].to_numpy(dtype=float)
    if target < x[0] - 1e-9 or target > x[-1] + 1e-9:
        return {"n": math.nan, "k": math.nan, "source": "范围外", "in_range": False}
    exact = np.where(np.isclose(x, target, rtol=0, atol=1e-9))[0]
    source = "原始值" if exact.size else "插值值"
    if exact.size:
        row = sample.clean.iloc[int(exact[0])]
        n, k = float(row["n"]), float(row["k"])
    else:
        n = float(np.interp(target, x, sample.clean["n"].to_numpy(dtype=float)))
        k = float(np.interp(target, x, sample.clean["k"].to_numpy(dtype=float)))
    return {"n": n, "k": k, "source": source, "in_range": True}


def band_values(sample: SampleData, start: float, end: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """截取波段并补入范围内边界插值点，用于均值与曲线面积。"""

    x = sample.clean["Wavelength_nm"].to_numpy(dtype=float)
    n = sample.clean["n"].to_numpy(dtype=float)
    k = sample.clean["k"].to_numpy(dtype=float)
    lo, hi = max(start, x[0]), min(end, x[-1])
    if lo >= hi:
        return np.array([]), np.array([]), np.array([])
    mask = (x >= lo) & (x <= hi)
    xb, nb, kb = x[mask], n[mask], k[mask]
    points_x = list(xb)
    points_n = list(nb)
    points_k = list(kb)
    for edge in (lo, hi):
        if not np.any(np.isclose(xb, edge, atol=1e-9, rtol=0)):
            points_x.append(float(edge))
            points_n.append(float(np.interp(edge, x, n)))
            points_k.append(float(np.interp(edge, x, k)))
    order = np.argsort(points_x)
    return (
        np.asarray(points_x)[order],
        np.asarray(points_n)[order],
        np.asarray(points_k)[order],
    )


def comparison_metrics(values: np.ndarray, reference: np.ndarray) -> dict[str, float]:
    """只在两个样品共同有效的网格点上计算差异统计。"""

    mask = np.isfinite(values) & np.isfinite(reference)
    if not np.any(mask):
        return {name: math.nan for name in ("rmse", "mae", "max_abs", "corr")}
    a, b = values[mask], reference[mask]
    diff = a - b
    corr = (
        float(np.corrcoef(a, b)[0, 1])
        if a.size >= 2 and np.ptp(a) > 0 and np.ptp(b) > 0
        else math.nan
    )
    return {
        "rmse": float(np.sqrt(np.mean(diff**2))),
        "mae": float(np.mean(np.abs(diff))),
        "max_abs": float(np.max(np.abs(diff))),
        "corr": corr,
    }


def trapezoid_integral(values: np.ndarray, coordinates: np.ndarray) -> float:
    """兼容 NumPy 1.26 与 2.x 的梯形积分。"""

    function = getattr(np, "trapezoid", None)
    if function is not None:
        return float(function(values, coordinates))
    return float(np.trapz(values, coordinates))


def compute_statistics(
    samples: list[SampleData],
    interpolated: dict[str, dict[str, np.ndarray]],
    reference: ReferenceSource,
    cfg: Config,
) -> list[dict[str, Any]]:
    """计算逐样品统计和参考数据源对比。"""

    rows: list[dict[str, Any]] = []
    ref_interp = interpolated[reference.sample_id]
    for sample in [s for s in samples if s.included]:
        df = sample.clean
        x = df["Wavelength_nm"].to_numpy(dtype=float)
        n = df["n"].to_numpy(dtype=float)
        k = df["k"].to_numpy(dtype=float)
        spacing = np.diff(x)
        n_min_idx, n_max_idx = int(np.argmin(n)), int(np.argmax(n))
        k_min_idx, k_max_idx = int(np.argmin(k)), int(np.argmax(k))
        row: dict[str, Any] = {
            "Sample_ID": sample.sample_id,
            "波长起点_nm": float(x[0]),
            "波长终点_nm": float(x[-1]),
            "有效数据点数": int(len(df)),
            "平均采样间隔_nm": float(np.mean(spacing)) if spacing.size else math.nan,
            "最大波长间隔_nm": float(np.max(spacing)) if spacing.size else math.nan,
            "n最小值": float(n[n_min_idx]),
            "n最小值波长_nm": float(x[n_min_idx]),
            "n最大值": float(n[n_max_idx]),
            "n最大值波长_nm": float(x[n_max_idx]),
            "k最小值": float(k[k_min_idx]),
            "k最小值波长_nm": float(x[k_min_idx]),
            "k最大值": float(k[k_max_idx]),
            "k最大值波长_nm": float(x[k_max_idx]),
        }
        for band, (start, end) in cfg.bands.items():
            xb, nb, kb = band_values(sample, start, end)
            row[f"{band}_平均n"] = float(np.mean(nb)) if nb.size else math.nan
            row[f"{band}_平均k"] = float(np.mean(kb)) if kb.size else math.nan
            row[f"{band}_n面积_nm"] = trapezoid_integral(nb, xb) if xb.size >= 2 else math.nan
            row[f"{band}_k面积_nm"] = trapezoid_integral(kb, xb) if xb.size >= 2 else math.nan
        n_cmp = comparison_metrics(interpolated[sample.sample_id]["n"], ref_interp["n"])
        k_cmp = comparison_metrics(interpolated[sample.sample_id]["k"], ref_interp["k"])
        row.update(
            {
                "n_RMSE": n_cmp["rmse"],
                "n_MAE": n_cmp["mae"],
                "n最大绝对差": n_cmp["max_abs"],
                "k_RMSE": k_cmp["rmse"],
                "k_MAE": k_cmp["mae"],
                "k最大绝对差": k_cmp["max_abs"],
                "n相关系数": n_cmp["corr"],
                "k相关系数": k_cmp["corr"],
                "综合质量状态": sample.qc["overall"],
            }
        )
        rows.append(row)
    return rows


def _fallback_peaks(y: np.ndarray, prominence: float, distance: int) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """无 SciPy 时的透明局部极值后备算法。"""

    if len(y) < 3:
        return np.array([], dtype=int), {"prominences": np.array([]), "widths": np.array([])}
    candidates = np.where((y[1:-1] > y[:-2]) & (y[1:-1] >= y[2:]))[0] + 1
    kept: list[int] = []
    prominences: list[float] = []
    half_window = max(3, distance * 2)
    for idx in candidates:
        lo, hi = max(0, idx - half_window), min(len(y), idx + half_window + 1)
        base = max(float(np.min(y[lo : idx + 1])), float(np.min(y[idx:hi])))
        prom = float(y[idx] - base)
        if prom >= prominence and all(abs(idx - old) >= distance for old in kept):
            kept.append(int(idx))
            prominences.append(prom)
    return (
        np.asarray(kept, dtype=int),
        {"prominences": np.asarray(prominences), "widths": np.full(len(kept), np.nan)},
    )


def analyze_features(sample: SampleData, cfg: Config) -> list[dict[str, Any]]:
    """识别全局极值和主要局部峰谷，默认基于原始清洗曲线。"""

    x = sample.clean["Wavelength_nm"].to_numpy(dtype=float)
    signal_source = "原始清洗曲线（重复波长取均值）"
    results: list[dict[str, Any]] = []
    for signal_name in ("n", "k"):
        original = sample.clean[signal_name].to_numpy(dtype=float)
        y = original.copy()
        if cfg.enable_smoothing:
            window = int(cfg.smoothing_window)
            if window % 2 == 0:
                window += 1
            if window > len(y):
                window = len(y) if len(y) % 2 == 1 else len(y) - 1
            if window >= 5 and _scipy_available():
                y = savgol_filter(y, window_length=window, polyorder=min(3, window - 2))
                signal_source = f"Savitzky–Golay 平滑曲线（窗口={window}）；原始值保留"
            else:
                signal_source = "原始清洗曲线（未满足平滑条件或 SciPy 不可用）"
        for kind, idx in (("全局最大值", int(np.argmax(y))), ("全局最小值", int(np.argmin(y)))):
            results.append(
                {
                    "Sample_ID": sample.sample_id,
                    "参数": signal_name,
                    "特征类型": kind,
                    "波长_nm": float(x[idx]),
                    "特征值": float(y[idx]),
                    "原始值": float(original[idx]),
                    "显著性": math.nan,
                    "峰宽_nm": math.nan,
                    "曲线来源": signal_source,
                }
            )
        searches = [("主要局部峰", y)]
        if signal_name == "n":
            searches.append(("主要局部谷", -y))
        signal_range = float(np.ptp(y))
        prominence = max(signal_range * cfg.peak_prominence_fraction, np.finfo(float).eps)
        distance = max(3, len(y) // 200)
        for feature_type, search_y in searches:
            if _scipy_available():
                peaks, props = find_peaks(search_y, prominence=prominence, distance=distance)
                if len(peaks):
                    width_info=peak_widths(search_y,peaks,rel_height=0.5)
                    widths=np.interp(width_info[3],np.arange(len(x)),x)-np.interp(width_info[2],np.arange(len(x)),x)
                else:widths=np.array([])
            else:
                peaks, props = _fallback_peaks(search_y, prominence, distance)
                widths = props.get("widths", np.full(len(peaks), np.nan))
            prominences = props.get("prominences", np.full(len(peaks), np.nan))
            order = np.argsort(np.nan_to_num(prominences, nan=-np.inf))[::-1][
                : cfg.max_features_per_signal
            ]
            for pos in order:
                idx = int(peaks[pos])
                width_nm = (
                    float(widths[pos])
                    if pos < len(widths) and np.isfinite(widths[pos])
                    else math.nan
                )
                results.append(
                    {
                        "Sample_ID": sample.sample_id,
                        "参数": signal_name,
                        "特征类型": feature_type,
                        "波长_nm": float(x[idx]),
                        "特征值": float(y[idx]),
                        "原始值": float(original[idx]),
                        "显著性": float(prominences[pos]) if np.isfinite(prominences[pos]) else math.nan,
                        "峰宽_nm": width_nm,
                        "曲线来源": signal_source,
                    }
                )
    return results


def compute_repeatability(
    samples: list[SampleData],
    grid: np.ndarray,
    interpolated: dict[str, dict[str, np.ndarray]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """计算同组重复测量的均值、样本标准差与摘要指标。

    只使用“是否纳入分析=是”且“纳入重复性分析=是”的样品。每个波长必须
    具备该组全部有效重复数据才参与均值/标准差，空白不会当作零。
    """

    grouped_all: dict[str, list[SampleData]] = {}
    for sample in samples:
        group = str(sample.metadata.get("分组", "") or "").strip()
        if not group or group == "未分组":
            continue
        grouped_all.setdefault(group, []).append(sample)

    curves: dict[str, dict[str, Any]] = {}
    summary: list[dict[str, Any]] = []
    for group, all_members in sorted(grouped_all.items()):
        members = [
            sample
            for sample in all_members
            if sample.included
            and sample.repeatability_included
            and sample.sample_id in interpolated
        ]
        if len(members) < 2:
            continue
        stack_n = np.vstack([interpolated[s.sample_id]["n"] for s in members])
        stack_k = np.vstack([interpolated[s.sample_id]["k"] for s in members])
        complete = np.all(np.isfinite(stack_n) & np.isfinite(stack_k), axis=0)
        n_mean = np.full(len(grid), np.nan)
        n_std = np.full(len(grid), np.nan)
        k_mean = np.full(len(grid), np.nan)
        k_std = np.full(len(grid), np.nan)
        if np.any(complete):
            n_mean[complete] = np.mean(stack_n[:, complete], axis=0)
            n_std[complete] = np.std(stack_n[:, complete], axis=0, ddof=1)
            k_mean[complete] = np.mean(stack_k[:, complete], axis=0)
            k_std[complete] = np.std(stack_k[:, complete], axis=0, ddof=1)
        valid_idx = np.where(complete)[0]
        common_start = float(grid[valid_idx[0]]) if valid_idx.size else math.nan
        common_end = float(grid[valid_idx[-1]]) if valid_idx.size else math.nan
        n_max_idx = (
            int(valid_idx[np.nanargmax(n_std[valid_idx])]) if valid_idx.size else None
        )
        k_max_idx = (
            int(valid_idx[np.nanargmax(k_std[valid_idx])]) if valid_idx.size else None
        )
        curves[group] = {
            "members": [s.sample_id for s in members],
            "n_mean": n_mean,
            "n_std": n_std,
            "k_mean": k_mean,
            "k_std": k_std,
        }
        summary.append(
            {
                "分组": group,
                "重复数": len(members),
                "排除数": len(all_members) - len(members),
                "共同有效点": int(np.sum(complete)),
                "共同起始波长_nm": common_start,
                "共同终止波长_nm": common_end,
                "平均σ(n)": float(np.nanmean(n_std)) if valid_idx.size else math.nan,
                "最大σ(n)": float(n_std[n_max_idx]) if n_max_idx is not None else math.nan,
                "最大σ(n)波长_nm": float(grid[n_max_idx]) if n_max_idx is not None else math.nan,
                "平均σ(k)": float(np.nanmean(k_std)) if valid_idx.size else math.nan,
                "最大σ(k)": float(k_std[k_max_idx]) if k_max_idx is not None else math.nan,
                "最大σ(k)波长_nm": float(grid[k_max_idx]) if k_max_idx is not None else math.nan,
            }
        )
    return curves, summary


def group_mean_source_id(group: str) -> str:
    """返回 Dashboard 中可辨识的组均值数据源名称。"""

    return f"组均值｜{group}"


def add_group_mean_sources(
    interpolated: dict[str, dict[str, np.ndarray]],
    repeatability_curves: dict[str, dict[str, Any]],
) -> None:
    """把组均值曲线加入统一数据源字典，供参考对比和看板共同使用。"""

    for group, curve in repeatability_curves.items():
        source_id = group_mean_source_id(group)
        n_values = np.asarray(curve["n_mean"], dtype=float)
        k_values = np.asarray(curve["k_mean"], dtype=float)
        valid = np.isfinite(n_values) & np.isfinite(k_values)
        interpolated[source_id] = {
            "n": n_values,
            "k": k_values,
            "source": np.where(valid, "组均值", "范围外").astype(object),
            "in_range": valid,
        }


def resolve_reference_source(
    samples: list[SampleData],
    interpolated: dict[str, dict[str, np.ndarray]],
    repeatability_curves: dict[str, dict[str, Any]],
    fallback_sample: SampleData,
    cfg: Config,
) -> ReferenceSource:
    """将用户选择解析为单独样品或组均值参考数据源。"""

    requested = str(cfg.reference_sample or "").strip()
    group_ids = {
        group_mean_source_id(group): group for group in repeatability_curves
    }
    if requested not in interpolated:
        requested = fallback_sample.sample_id
    if requested in group_ids:
        reference = ReferenceSource(
            source_id=requested,
            source_type="组均值",
            group_name=group_ids[requested],
        )
    else:
        reference = ReferenceSource(
            source_id=requested,
            source_type="单独样品",
        )
    for sample in samples:
        sample.is_reference = (
            reference.source_type == "单独样品"
            and sample.sample_id == reference.source_id
        )
    cfg.reference_sample = reference.source_id
    return reference


def value_from_grid_curve(
    grid: np.ndarray, values: np.ndarray, target: float
) -> float:
    """在有效网格范围内读取或线性插值，不跨越范围外空白。"""

    valid = np.isfinite(values)
    if not np.any(valid):
        return math.nan
    x = grid[valid]
    y = values[valid]
    if target < x[0] - 1e-9 or target > x[-1] + 1e-9:
        return math.nan
    exact = np.where(np.isclose(x, target, atol=1e-9, rtol=0))[0]
    if exact.size:
        return float(y[exact[0]])
    return float(np.interp(target, x, y))


def analyze_uv_k_peak_arrays(
    x_all: np.ndarray,
    k_all: np.ndarray,
    cfg: Config,
    source_label: str,
) -> dict[str, Any]:
    """在给定曲线上提取 UV 区最显著的 k 峰。"""

    uv_start, uv_end = cfg.bands.get("UV", (190.0, 400.0))
    valid = (
        np.isfinite(x_all)
        & np.isfinite(k_all)
        & (x_all >= uv_start)
        & (x_all <= uv_end)
    )
    x = np.asarray(x_all[valid], dtype=float)
    original = np.asarray(k_all[valid], dtype=float)
    if x.size == 0:
        return {
            "peak_nm": math.nan,
            "peak_k": math.nan,
            "prominence": math.nan,
            "source": f"{source_label}；UV范围外",
        }
    y = original.copy()
    source = source_label
    if cfg.enable_smoothing and _scipy_available():
        window = int(cfg.smoothing_window)
        if window % 2 == 0:
            window += 1
        if window > len(y):
            window = len(y) if len(y) % 2 == 1 else len(y) - 1
        if window >= 5:
            y = savgol_filter(y, window_length=window, polyorder=min(3, window - 2))
            source = f"{source_label}；Savitzky–Golay平滑（窗口{window}）"
    prominence_limit = max(
        float(np.ptp(y)) * cfg.peak_prominence_fraction,
        np.finfo(float).eps,
    )
    distance = max(3, len(y) // 100)
    if _scipy_available():
        peaks, props = find_peaks(y, prominence=prominence_limit, distance=distance)
    else:
        peaks, props = _fallback_peaks(y, prominence_limit, distance)
    if len(peaks):
        prominences = props.get("prominences", np.full(len(peaks), np.nan))
        position = int(np.nanargmax(np.nan_to_num(prominences, nan=-np.inf)))
        idx = int(peaks[position])
        prominence = (
            float(prominences[position])
            if np.isfinite(prominences[position])
            else math.nan
        )
        algorithm = f"{source}；最显著局部峰"
    else:
        idx = int(np.argmax(y))
        prominence = math.nan
        algorithm = f"{source}；无显著局部峰，列出UV全局最大值"
    return {
        "peak_nm": float(x[idx]),
        "peak_k": float(original[idx]),
        "prominence": prominence,
        "source": algorithm,
    }


def compute_uv_k_peak_summary(
    samples: list[SampleData],
    reference: ReferenceSource,
    grid: np.ndarray,
    interpolated: dict[str, dict[str, np.ndarray]],
    cfg: Config,
) -> list[dict[str, Any]]:
    """提取每个样品 UV 区主要 k 峰及相对参考数据源变化。"""

    def one_peak(sample: SampleData) -> dict[str, Any]:
        df = sample.clean
        return analyze_uv_k_peak_arrays(
            df["Wavelength_nm"].to_numpy(dtype=float),
            df["k"].to_numpy(dtype=float),
            cfg,
            "原始清洗曲线",
        )

    peak_by_id = {
        sample.sample_id: one_peak(sample)
        for sample in samples
        if sample.included
    }
    if reference.sample_id in peak_by_id:
        reference_peak = peak_by_id[reference.sample_id]
    else:
        reference_peak = analyze_uv_k_peak_arrays(
            grid,
            interpolated[reference.sample_id]["k"],
            cfg,
            "组均值曲线",
        )
    rows: list[dict[str, Any]] = []
    for sample in [s for s in samples if s.included]:
        peak = peak_by_id[sample.sample_id]
        rows.append(
            {
                "Sample_ID": sample.sample_id,
                "UV主要峰波长_nm": peak["peak_nm"],
                "峰值k": peak["peak_k"],
                "峰显著性": peak["prominence"],
                "相对参考峰位偏移_nm": (
                    peak["peak_nm"] - reference_peak["peak_nm"]
                    if np.isfinite(peak["peak_nm"])
                    and np.isfinite(reference_peak["peak_nm"])
                    else math.nan
                ),
                "相对参考峰高变化": (
                    peak["peak_k"] - reference_peak["peak_k"]
                    if np.isfinite(peak["peak_k"])
                    and np.isfinite(reference_peak["peak_k"])
                    else math.nan
                ),
                "曲线与算法": peak["source"],
            }
        )
    return rows



KEY_METRICS = [
    "n",
    "k",
    "epsilon1",
    "epsilon2",
    "alpha_cm^-1",
    "R_理论值",
    "delta_nm",
    "数据来源",
    "处于实测范围",
    "Δn",
    "Δk",
    "n相对偏差",
    "k相对偏差",
]


def build_key_summary(
    samples: list[SampleData],
    reference: ReferenceSource,
    grid: np.ndarray,
    interpolated: dict[str, dict[str, np.ndarray]],
    cfg: Config,
) -> tuple[list[list[Any]], dict[tuple[str, float], dict[str, Any]]]:
    rows: list[list[Any]] = []
    cache: dict[tuple[str, float], dict[str, Any]] = {}
    reference_targets: dict[float, dict[str, Any]] = {}
    reference_curve = interpolated[reference.sample_id]
    for target in cfg.target_wavelengths_nm:
        target_value = float(target)
        n_value = value_from_grid_curve(grid, reference_curve["n"], target_value)
        k_value = value_from_grid_curve(grid, reference_curve["k"], target_value)
        reference_targets[target] = {
            "n": n_value,
            "k": k_value,
            "in_range": bool(np.isfinite(n_value) and np.isfinite(k_value)),
        }
    for sample in [s for s in samples if s.included]:
        row: list[Any] = [
            sample.sample_id,
            sample.file_name,
            sample.metadata.get("样品名称", ""),
            sample.metadata.get("分组", ""),
        ]
        for target in cfg.target_wavelengths_nm:
            result = value_at_target(sample, target)
            ref = reference_targets[target]
            if result["in_range"]:
                der = derive_optical(
                    np.array([target]),
                    np.array([result["n"]]),
                    np.array([result["k"]]),
                    cfg.k_zero_threshold,
                )
                delta_n = (
                    result["n"] - ref["n"] if ref["in_range"] and np.isfinite(ref["n"]) else math.nan
                )
                delta_k = (
                    result["k"] - ref["k"] if ref["in_range"] and np.isfinite(ref["k"]) else math.nan
                )
                rel_n = delta_n / ref["n"] if ref["in_range"] and abs(ref["n"]) > 0 else math.nan
                rel_k = (
                    delta_k / ref["k"]
                    if ref["in_range"] and abs(ref["k"]) > cfg.k_zero_threshold
                    else math.nan
                )
                values = [
                    result["n"],
                    result["k"],
                    der["epsilon1"][0],
                    der["epsilon2"][0],
                    der["alpha"][0],
                    der["R"][0],
                    der["delta"][0],
                    result["source"],
                    "是",
                    delta_n,
                    delta_k,
                    rel_n,
                    rel_k,
                ]
            else:
                values = [math.nan] * 7 + ["范围外", "否"] + [math.nan] * 4
            cache[(sample.sample_id, float(target))] = {
                key: value for key, value in zip(KEY_METRICS, values)
            }
            row.extend(values)
        rows.append(row)
    return rows, cache




def is_analysis_workbook(path):
    path=Path(path)
    if path.suffix.lower() not in {'.xlsx','.xlsm'}:return False
    try:
        with ZipFile(path) as z:
            xml=ET.fromstring(z.read('xl/workbook.xml'))
            names={s.get('name') for s in xml.findall('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheets/*')}
            core=ET.fromstring(z.read('docProps/core.xml'))
            title=core.find('{http://purl.org/dc/elements/1.1/}title')
            return title is not None and title.text=='NK Optical Analysis' and {'00_使用说明','03_原始数据','11_图表看板'}<=names
    except (BadZipFile,KeyError,ET.ParseError,OSError):
        return False
