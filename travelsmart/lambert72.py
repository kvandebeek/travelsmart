"""Belgian Lambert 72 (EPSG:31370) to WGS84 latitude/longitude, without a projection library.

Inverse Lambert Conic Conformal (2SP) on the International 1924 ellipsoid gives BD72 coordinates; a 7-parameter
Helmert transformation (EPSG:15929, BD72 to WGS 84 (3), ~1 m) then shifts them to WGS84. The datum shift alone
is about 100 m, which matters when matching traffic events to roads.
"""
from __future__ import annotations

import math

# EPSG:31370 projection parameters
_A, _F = 6378388.0, 1 / 297.0                          # International 1924
_E2 = _F * (2 - _F)
_E = math.sqrt(_E2)
_LAT1, _LAT2 = math.radians(51.16666723333333), math.radians(49.8333339)
_LON0 = math.radians(4.367486666666666)
_X0, _Y0 = 150000.013, 5400088.438                     # false easting / northing (latitude of origin 90°)

def _m(lat): return math.cos(lat) / math.sqrt(1 - _E2 * math.sin(lat) ** 2)
def _t(lat): return math.tan(math.pi / 4 - lat / 2) / ((1 - _E * math.sin(lat)) / (1 + _E * math.sin(lat))) ** (_E / 2)

_N = (math.log(_m(_LAT1)) - math.log(_m(_LAT2))) / (math.log(_t(_LAT1)) - math.log(_t(_LAT2)))
_G = _m(_LAT1) / (_N * _t(_LAT1) ** _N)

# BD72 -> WGS84, position-vector convention (EPSG:15929 is published in coordinate-frame form: rotations negated)
_TX, _TY, _TZ = -106.8686, 52.2978, -103.7239
_RX, _RY, _RZ = (math.radians(s / 3600) for s in (0.3366, -0.457, 1.8422))
_S = -1.2747e-6
_WGS_A, _WGS_F = 6378137.0, 1 / 298.257223563


def to_bd72(x: float, y: float) -> tuple[float, float]:
    """Lambert 72 easting/northing (m) to BD72 latitude/longitude in degrees."""
    dx, dy = x - _X0, _Y0 - y   # the latitude of origin is the pole: rho is measured from there
    rho = math.copysign(math.hypot(dx, dy), _N)
    t = (rho / (_A * _G)) ** (1 / _N)
    theta = math.atan2(dx, dy)
    lat = math.pi / 2 - 2 * math.atan(t)
    for _ in range(10):
        lat = math.pi / 2 - 2 * math.atan(t * ((1 - _E * math.sin(lat)) / (1 + _E * math.sin(lat))) ** (_E / 2))
    return math.degrees(lat), math.degrees(theta / _N + _LON0)


def _geocentric(lat, lon, a, e2):
    lat, lon = math.radians(lat), math.radians(lon)
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    return n * math.cos(lat) * math.cos(lon), n * math.cos(lat) * math.sin(lon), n * (1 - e2) * math.sin(lat)


def _geodetic(x, y, z, a, e2):
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - e2))
    for _ in range(10):
        n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        lat = math.atan2(z + e2 * n * math.sin(lat), p)
    return math.degrees(lat), math.degrees(lon)


def to_wgs84(x: float, y: float) -> tuple[float, float]:
    """Lambert 72 easting/northing (m) to WGS84 (latitude, longitude) in degrees."""
    X, Y, Z = _geocentric(*to_bd72(x, y), _A, _E2)
    k = 1 + _S
    X, Y, Z = (_TX + k * (X - _RZ * Y + _RY * Z),
               _TY + k * (_RZ * X + Y - _RX * Z),
               _TZ + k * (-_RY * X + _RX * Y + Z))
    return _geodetic(X, Y, Z, _WGS_A, _WGS_F * (2 - _WGS_F))
