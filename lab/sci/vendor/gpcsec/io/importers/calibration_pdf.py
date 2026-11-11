"""Evidence-preserving Cirrus-style narrow-standard calibration PDF importer."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader

from gpcsec.core.calibration import fit_polynomial
from gpcsec.models import CalibrationPoint, CalibrationRecord, RawFileRecord


@dataclass(frozen=True, slots=True)
class CalibrationPdfProbe:
    page_count: int
    point_count: int
    reported_degree: int
    extracted_text: str


def parse_calibration_text(text: str) -> tuple[tuple[CalibrationPoint, ...], int]:
    if "Calibration Points" not in text:
        raise ValueError("PDF text does not contain a calibration point table")
    table = text.split("Calibration Points", 1)[1]
    point_pattern = re.compile(
        r"^\s*(\d+)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+[-+]?\d+(?:\.\d+)?\s+Y\s*es\b",
        re.IGNORECASE | re.MULTILINE,
    )
    matches = point_pattern.findall(table)
    points = tuple(CalibrationPoint(float(rt), float(mw)) for _, rt, mw in matches)
    degree_match = re.search(r"Curve Fit Used:\s*(\d+)", text, re.IGNORECASE)
    if degree_match is None:
        raise ValueError("PDF text does not declare the calibration polynomial degree")
    degree = int(degree_match.group(1))
    if len(points) <= degree:
        raise ValueError("PDF contains too few enabled calibration points")
    return points, degree


def import_calibration_pdf_bytes(
    raw: bytes, source_name: str, calibration_id: str, name: str | None = None
) -> tuple[CalibrationRecord, CalibrationPdfProbe]:
    reader = PdfReader(BytesIO(raw))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    points, degree = parse_calibration_text(text)
    digest = hashlib.sha256(raw).hexdigest()
    raw_file = RawFileRecord(source_name, digest, len(raw), raw)
    fitted = fit_polynomial(points, degree, digest)
    if fitted.statistics is None:
        raise ValueError("calibration fit did not produce statistics")
    record = CalibrationRecord(
        calibration_id=calibration_id,
        name=name or source_name.rsplit(".", 1)[0],
        raw_file=raw_file,
        points=points,
        degree=degree,
        coefficients=fitted.coefficients,
        scaled_coefficients=fitted.scaled_coefficients,
        valid_rt_min=fitted.valid_rt_min,
        valid_rt_max=fitted.valid_rt_max,
        monotonic_direction=fitted.monotonic_direction,
        statistics=fitted.statistics,
        algorithm_version=fitted.algorithm_version,
    )
    return record, CalibrationPdfProbe(len(reader.pages), len(points), degree, text)
