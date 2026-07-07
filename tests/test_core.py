"""Testy remach_lib.core — wartości punktowe (h=11000 m, V=100 m/s, L=1 m)
i inwersje round-trip (S5 z audytu AnberReMach)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remach_lib import core  # noqa: E402

H, V, L = 11000.0, 100.0, 1.0


def test_point_h11000_values():
    r = core.point(V=V, L=L, h_geo=H)
    # ISA @ h_geo=11000 m (ISO 2533): T=216.65 K, a=295.07 m/s
    assert r['T_K'] == pytest.approx(216.65, abs=0.01)
    assert r['a'] == pytest.approx(295.07, abs=0.1)
    # spójność wewnętrzna definicji
    assert r['Ma'] == pytest.approx(V / r['a'], rel=1e-12)
    assert r['Re'] == pytest.approx(V * L / r['nu'], rel=1e-12)
    assert r['q_Pa'] == pytest.approx(0.5 * r['rho'] * V * V, rel=1e-12)
    # wartości tablicowe
    assert r['Ma'] == pytest.approx(0.3389, abs=5e-4)
    assert r['Re'] == pytest.approx(2.56e6, rel=0.02)   # nu ~ 3.90e-5 m^2/s


def test_inversion_roundtrip():
    r = core.point(V=V, L=L, h_geo=H)
    assert core.v_for_reynolds(r['Re'], L, H) == pytest.approx(V, rel=1e-9)
    assert core.v_for_mach(r['Ma'], H) == pytest.approx(V, rel=1e-9)
    assert core.length_for_reynolds(r['Re'], V, H) == pytest.approx(L, rel=1e-9)
    # bisekcja h(Re) — tol względne (N1)
    assert core.h_for_reynolds(r['Re'], V, L) == pytest.approx(H, abs=1.0)


def test_h_for_reynolds_out_of_range():
    with pytest.raises(ValueError):
        core.h_for_reynolds(1e12, V, L)   # nieosiągalne w 0..80 km


def test_reynolds_accepts_v_zero():
    # N9: V=0 jest poprawne fizycznie (Re=0); tylko L i nu muszą być >0
    assert core.reynolds(0.0, 1.0, 1e-5) == 0.0
