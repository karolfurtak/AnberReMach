"""Implementacja Re/Ma/q/EAS oparta o isa_lib (atmosfera/isa_lib)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict

# Import isa_lib z siostrzanego apps/atmosfera/
_LIB_PARENT = Path(__file__).resolve().parents[1]  # anberremach/ (deployed) lub apps/remach/ (dev)
if str(_LIB_PARENT) not in sys.path:
    sys.path.insert(0, str(_LIB_PARENT))

from isa_lib import isa  # noqa: E402
from isa_lib import constants as C  # noqa: E402


# ── kinematyka i dynamika ───────────────────────────────────────────────────
def reynolds(V: float, L: float, nu: float) -> float:
    """Re = V·L/ν  (ν = lepkość kinematyczna)."""
    if nu <= 0 or L <= 0:
        raise ValueError(f'L i ν muszą być >0 (L={L}, ν={nu})')
    return V * L / nu


def mach(V: float, a: float) -> float:
    """Ma = V/a."""
    if a <= 0:
        raise ValueError(f'a musi być >0 (a={a})')
    return V / a


def dyn_pressure(rho: float, V: float) -> float:
    """q = ½·ρ·V² [Pa]."""
    return 0.5 * rho * V * V


def eas(V_tas: float, rho: float, rho0: float = C.rho0_SL) -> float:
    """EAS = V_TAS·√(ρ/ρ0)."""
    if rho0 <= 0:
        raise ValueError('rho0 musi być >0')
    return V_tas * (rho / rho0) ** 0.5


def kinematic_visc_at_h(h_geo: float, dT: float = 0.0) -> float:
    """ν = μ/ρ z modelu ISA na zadanej wysokości."""
    r = isa(h_geo, dT=dT)
    return r['mu_Pa_s'] / r['rho_kg_m3']


# ── obliczenia punktowe ─────────────────────────────────────────────────────
def point(V: float, L: float, h_geo: float, dT: float = 0.0) -> Dict:
    """Pełny zestaw parametrów dla zadanych (V, L, h, ΔT).

    Zwraca słownik:
      h_geo, T_K, p_Pa, rho, a, mu, nu, V, L, Re, Ma, q_Pa, EAS, sigma
    """
    r = isa(h_geo, dT=dT)
    nu = r['mu_Pa_s'] / r['rho_kg_m3']
    Re = reynolds(V, L, nu)
    Ma = mach(V, r['a_m_s'])
    q = dyn_pressure(r['rho_kg_m3'], V)
    E = eas(V, r['rho_kg_m3'])
    return {
        'h_geo_m': r['h_geo_m'],
        'T_K':     r['T_K'],
        'p_Pa':    r['p_Pa'],
        'rho':     r['rho_kg_m3'],
        'a':       r['a_m_s'],
        'mu':      r['mu_Pa_s'],
        'nu':      nu,
        'V':       V,
        'L':       L,
        'Re':      Re,
        'Ma':      Ma,
        'q_Pa':    q,
        'EAS':     E,
        'sigma':   r['sigma'],
        'dT':      dT,
    }


# ── inwersje analityczne ────────────────────────────────────────────────────
def v_for_reynolds(Re: float, L: float, h_geo: float, dT: float = 0.0) -> float:
    """V = Re·ν/L."""
    if Re <= 0 or L <= 0:
        raise ValueError('Re i L muszą być >0')
    nu = kinematic_visc_at_h(h_geo, dT=dT)
    return Re * nu / L


def v_for_mach(Ma: float, h_geo: float, dT: float = 0.0) -> float:
    """V = Ma·a."""
    if Ma < 0:
        raise ValueError('Ma musi być ≥0')
    a = isa(h_geo, dT=dT)['a_m_s']
    return Ma * a


def length_for_reynolds(Re: float, V: float, h_geo: float, dT: float = 0.0) -> float:
    """L = Re·ν/V."""
    if Re <= 0 or V <= 0:
        raise ValueError('Re i V muszą być >0')
    nu = kinematic_visc_at_h(h_geo, dT=dT)
    return Re * nu / V


# ── inwersja wysokości: bisekcja ────────────────────────────────────────────
def h_for_reynolds(Re_target: float, V: float, L: float,
                   dT: float = 0.0,
                   h_lo: float = 0.0, h_hi: float = 80000.0,
                   tol: float = 1e-6, max_iter: int = 80) -> float:
    """Bisekcja: h takie, że Re(V,L,h,ΔT) ≈ Re_target.

    Re jest monotonicznie malejące z h (ν rośnie z h aż do ~85 km).
    `tol` jest WZGLĘDNE: stop gdy |Re(m) − Re_target| < tol·Re_target
    lub gdy szerokość przedziału < 1e-3 m.
    Zwraca h_geo [m]; podnosi ValueError gdy Re_target poza zakresem
    osiągalnym w paśmie [h_lo, h_hi].
    """
    if Re_target <= 0 or V <= 0 or L <= 0:
        raise ValueError('Re, V, L muszą być >0')

    def f(h: float) -> float:
        nu = kinematic_visc_at_h(h, dT=dT)
        return V * L / nu - Re_target

    f_lo = f(h_lo)
    f_hi = f(h_hi)
    if f_lo * f_hi > 0:
        raise ValueError(
            f'Re_target={Re_target:.3e} poza zakresem osiągalnym '
            f'(h={h_lo:.0f}..{h_hi:.0f}; Re={Re_target+f_lo:.3e}..{Re_target+f_hi:.3e})'
        )

    a_, b_ = h_lo, h_hi
    fa = f_lo   # N1: pamiętamy f(a_) — bez podwójnego liczenia f w pętli
    for _ in range(max_iter):
        m = 0.5 * (a_ + b_)
        fm = f(m)
        if abs(fm) < tol * Re_target or (b_ - a_) < 1e-3:
            return m
        if fa * fm < 0:
            b_ = m
        else:
            a_, fa = m, fm
    return 0.5 * (a_ + b_)
