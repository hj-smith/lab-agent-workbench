"""Certified narrow-standard lots kept separately from repeat calibration runs."""

from __future__ import annotations

import math
from dataclasses import dataclass

from gpcsec.models.domain import RawFileRecord


@dataclass(frozen=True, slots=True)
class CertifiedStandardComponent:
    """One certified polymer component contained in a mixed standard vial."""

    component_id: str
    vial_code: str
    ordinal: int
    mp_g_mol: float
    mn_g_mol: float | None = None
    mw_g_mol: float | None = None
    mw_light_scattering_g_mol: float | None = None
    dispersity: float | None = None
    intrinsic_viscosity_dl_g: float | None = None
    mass_per_vial_mg: float | None = None
    use_for_calibration: bool = True
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.component_id.strip() or not self.vial_code.strip() or self.ordinal < 1:
            raise ValueError("standard component identity is invalid")
        positive = (
            (self.mp_g_mol, "Mp"),
            (self.mn_g_mol, "Mn"),
            (self.mw_g_mol, "Mw"),
            (self.mw_light_scattering_g_mol, "light-scattering Mw"),
            (self.intrinsic_viscosity_dl_g, "intrinsic viscosity"),
            (self.mass_per_vial_mg, "mass per vial"),
        )
        for value, label in positive:
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"standard component {label} must be finite and positive")
        if self.dispersity is not None and (
            not math.isfinite(self.dispersity) or self.dispersity < 1
        ):
            raise ValueError("standard component dispersity must be finite and at least one")


@dataclass(frozen=True, slots=True)
class CertifiedStandardLot:
    """Stable certificate identity that may be reused by many calibration runs."""

    lot_id: str
    name: str
    polymer_type: str
    supplier: str
    product_name: str
    product_numbers: tuple[str, ...]
    batch_number: str
    certificate_reference: str
    certificate_file: RawFileRecord
    components: tuple[CertifiedStandardComponent, ...]
    issue_date: str = ""
    expiry_date: str = ""
    first_use_date: str = ""
    active: bool = True
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.lot_id.strip() or not self.name.strip() or not self.batch_number.strip():
            raise ValueError("standard lot identity is required")
        component_ids = [item.component_id for item in self.components]
        if not self.components or len(component_ids) != len(set(component_ids)):
            raise ValueError("standard lot requires uniquely identified components")
        positions = [(item.vial_code.casefold(), item.ordinal) for item in self.components]
        if len(positions) != len(set(positions)):
            raise ValueError("standard lot vial component positions must be unique")
