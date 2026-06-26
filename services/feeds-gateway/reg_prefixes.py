"""ICAO aircraft registration-prefix -> country.

Ported from intel/server.js so the gateway can derive REGISTERED_IN without a
round-trip when the captured graph lacks the edge. Try the 2-char prefix first,
then the 1-char prefix.
"""
from __future__ import annotations

REG_PREFIXES: dict[str, str] = {
    "N": "United States", "G": "United Kingdom", "F": "France", "D": "Germany", "I": "Italy",
    "JA": "Japan", "HL": "South Korea", "B": "China", "VT": "India", "TC": "Turkey",
    "SU": "Russia", "RA": "Russia", "UR": "Ukraine", "A6": "UAE", "A7": "Qatar", "9V": "Singapore",
    "VH": "Australia", "C": "Canada", "PP": "Brazil", "PR": "Brazil", "PT": "Brazil",
    "EC": "Spain", "PH": "Netherlands", "HS": "Thailand", "9M": "Malaysia", "PK": "Pakistan",
    "EP": "Iran", "YI": "Iraq", "HZ": "Saudi Arabia", "4X": "Israel", "SX": "Greece",
    "OE": "Austria", "HB": "Switzerland", "SE": "Sweden", "OH": "Finland", "LN": "Norway",
    "OY": "Denmark", "OO": "Belgium", "CS": "Portugal", "SP": "Poland",
    "OK": "Czech Republic", "HA": "Hungary", "YR": "Romania", "LZ": "Bulgaria",
    "EI": "Ireland", "EW": "Belarus", "ES": "Estonia", "YL": "Latvia", "LY": "Lithuania",
}


def country_from_registration(registration: str | None) -> str | None:
    if not registration:
        return None
    reg = registration.upper().strip()
    return REG_PREFIXES.get(reg[:2]) or REG_PREFIXES.get(reg[:1])
