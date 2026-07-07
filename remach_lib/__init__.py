"""AnberReMach — biblioteka liczb podobieństwa (Re, Ma) na atmosferze ISA.

Re  = V·L/ν              (Reynolds, ν = μ/ρ)
Ma  = V/a                (Mach, a = √(γ·R·T))
μ   = β·T^(3/2)/(T+S)    (Sutherland, ISO 2533 §3.7)
q   = ½·ρ·V²             (ciśnienie dynamiczne)
EAS = V·√σ               (ekwiwalentna prędkość, σ = ρ/ρ0)

Tryby inwersji:
  - V_dla_Re : zadane Re, L, h → V_TAS
  - V_dla_Ma : zadane Ma, h    → V_TAS
  - L_dla_Re : zadane Re, V, h → L
  - h_dla_Re : zadane Re, V, L → h  (binsekcja w pasie troposfery/stratosfery)

Wszystkie funkcje przyjmują wysokość geopotencjalną h_geo [m]
i opcjonalny ΔT [K] — przekazywane do `isa_lib.isa`.
"""
from .core import (
    reynolds, mach, dyn_pressure, eas, kinematic_visc_at_h,
    point, v_for_reynolds, v_for_mach, length_for_reynolds, h_for_reynolds,
)

__all__ = [
    'reynolds', 'mach', 'dyn_pressure', 'eas', 'kinematic_visc_at_h',
    'point', 'v_for_reynolds', 'v_for_mach', 'length_for_reynolds', 'h_for_reynolds',
]
