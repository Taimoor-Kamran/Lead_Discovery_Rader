"""Geohash cells for blocking, and great-circle distance for the proximity signal.

A precision-7 geohash cell is about 153 m across, which is the granularity the resolver
blocks on: two records in the same or a neighbouring cell are close enough to compare.
"""

import math

BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
GEOHASH_PRECISION = 7
EARTH_RADIUS_M = 6_371_008.8


def encode(lat: float, lng: float, precision: int = GEOHASH_PRECISION) -> str:
    """Standard geohash encoding. Out-of-range coordinates are rejected by the caller."""
    lat_range = [-90.0, 90.0]
    lng_range = [-180.0, 180.0]
    out: list[str] = []
    bits = 0
    bit_count = 0
    even = True

    while len(out) < precision:
        if even:
            mid = (lng_range[0] + lng_range[1]) / 2
            if lng > mid:
                bits = (bits << 1) | 1
                lng_range[0] = mid
            else:
                bits <<= 1
                lng_range[1] = mid
        else:
            mid = (lat_range[0] + lat_range[1]) / 2
            if lat > mid:
                bits = (bits << 1) | 1
                lat_range[0] = mid
            else:
                bits <<= 1
                lat_range[1] = mid
        even = not even
        bit_count += 1
        if bit_count == 5:
            out.append(BASE32[bits])
            bits = 0
            bit_count = 0
    return "".join(out)


def geohash7(lat: float | None, lng: float | None) -> str | None:
    if lat is None or lng is None:
        return None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
        return None
    return encode(lat, lng, GEOHASH_PRECISION)


def cell_size(precision: int = GEOHASH_PRECISION) -> tuple[float, float]:
    """Height and width of one cell in degrees, from how the bits are split."""
    lng_bits = (precision * 5 + 1) // 2
    lat_bits = precision * 5 // 2
    return 180.0 / (2**lat_bits), 360.0 / (2**lng_bits)


def neighbours(lat: float, lng: float, precision: int = GEOHASH_PRECISION) -> list[str]:
    """The cell containing the point and its eight neighbours.

    Offsetting by exactly one cell always lands in the adjacent cell, whatever the
    point's position inside its own, so no adjacency table is needed.
    """
    d_lat, d_lng = cell_size(precision)
    cells: list[str] = []
    for i in (-1, 0, 1):
        for j in (-1, 0, 1):
            near_lat = max(min(lat + i * d_lat, 90.0), -90.0)
            near_lng = (lng + j * d_lng + 180.0) % 360.0 - 180.0
            cell = encode(near_lat, near_lng, precision)
            if cell not in cells:
                cells.append(cell)
    return cells


def distance_m(
    lat1: float | None, lng1: float | None, lat2: float | None, lng2: float | None
) -> float | None:
    """Great-circle distance in metres, or `None` when either point is unknown."""
    if lat1 is None or lng1 is None or lat2 is None or lng2 is None:
        return None
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))
