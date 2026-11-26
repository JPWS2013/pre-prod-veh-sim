"""Offline geography: city coordinates, distances and address → delivery center lookup.

Coordinates are approximate city-center values, which is plenty for routing a
customer to the nearest delivery center and estimating truck/rail transit time.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Iterable, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from .models import Address, DeliveryCenter

# (city, state, lat, lon, relative demand weight ~ metro population in millions)
CITIES: list[tuple[str, str, float, float, float]] = [
    ("Seattle", "WA", 47.61, -122.33, 4.0),
    ("Spokane", "WA", 47.66, -117.43, 0.6),
    ("Portland", "OR", 45.52, -122.68, 2.5),
    ("San Francisco", "CA", 37.77, -122.42, 4.7),
    ("San Jose", "CA", 37.34, -121.89, 2.0),
    ("Sacramento", "CA", 38.58, -121.49, 2.4),
    ("Fresno", "CA", 36.74, -119.79, 1.0),
    ("Los Angeles", "CA", 34.05, -118.24, 13.0),
    ("San Diego", "CA", 32.72, -117.16, 3.3),
    ("Las Vegas", "NV", 36.17, -115.14, 2.3),
    ("Reno", "NV", 39.53, -119.81, 0.5),
    ("Phoenix", "AZ", 33.45, -112.07, 5.0),
    ("Tucson", "AZ", 32.22, -110.97, 1.0),
    ("Salt Lake City", "UT", 40.76, -111.89, 1.3),
    ("Boise", "ID", 43.62, -116.20, 0.8),
    ("Denver", "CO", 39.74, -104.99, 3.0),
    ("Albuquerque", "NM", 35.08, -106.65, 0.9),
    ("El Paso", "TX", 31.76, -106.49, 0.9),
    ("Billings", "MT", 45.78, -108.50, 0.2),
    ("Cheyenne", "WY", 41.14, -104.82, 0.1),
    ("Dallas", "TX", 32.78, -96.80, 7.9),
    ("Houston", "TX", 29.76, -95.37, 7.3),
    ("Austin", "TX", 30.27, -97.74, 2.4),
    ("San Antonio", "TX", 29.42, -98.49, 2.6),
    ("Oklahoma City", "OK", 35.47, -97.52, 1.4),
    ("Wichita", "KS", 37.69, -97.34, 0.65),
    ("Kansas City", "MO", 39.10, -94.58, 2.2),
    ("St. Louis", "MO", 38.63, -90.20, 2.8),
    ("Omaha", "NE", 41.26, -95.93, 1.0),
    ("Des Moines", "IA", 41.59, -93.62, 0.7),
    ("Minneapolis", "MN", 44.98, -93.27, 3.7),
    ("Fargo", "ND", 46.88, -96.79, 0.25),
    ("Sioux Falls", "SD", 43.54, -96.73, 0.3),
    ("Milwaukee", "WI", 43.04, -87.91, 1.6),
    ("Chicago", "IL", 41.88, -87.63, 9.4),
    ("Indianapolis", "IN", 39.77, -86.16, 2.1),
    ("Detroit", "MI", 42.33, -83.05, 4.3),
    ("Grand Rapids", "MI", 42.96, -85.67, 1.1),
    ("Columbus", "OH", 39.96, -83.00, 2.1),
    ("Cleveland", "OH", 41.50, -81.69, 2.1),
    ("Cincinnati", "OH", 39.10, -84.51, 2.2),
    ("Pittsburgh", "PA", 40.44, -79.99, 2.4),
    ("Louisville", "KY", 38.25, -85.76, 1.3),
    ("Nashville", "TN", 36.16, -86.78, 2.0),
    ("Memphis", "TN", 35.15, -90.05, 1.3),
    ("Little Rock", "AR", 34.75, -92.29, 0.75),
    ("New Orleans", "LA", 29.95, -90.07, 1.3),
    ("Jackson", "MS", 32.30, -90.18, 0.6),
    ("Birmingham", "AL", 33.52, -86.80, 1.1),
    ("Atlanta", "GA", 33.75, -84.39, 6.1),
    ("Charlotte", "NC", 35.23, -80.84, 2.7),
    ("Raleigh", "NC", 35.78, -78.64, 1.4),
    ("Charleston", "SC", 32.78, -79.93, 0.8),
    ("Jacksonville", "FL", 30.33, -81.66, 1.6),
    ("Orlando", "FL", 28.54, -81.38, 2.7),
    ("Tampa", "FL", 27.95, -82.46, 3.2),
    ("Miami", "FL", 25.76, -80.19, 6.1),
    ("Richmond", "VA", 37.54, -77.44, 1.3),
    ("Washington", "DC", 38.91, -77.04, 6.3),
    ("Baltimore", "MD", 39.29, -76.61, 2.8),
    ("Wilmington", "DE", 39.74, -75.55, 0.7),
    ("Philadelphia", "PA", 39.95, -75.17, 6.2),
    ("Newark", "NJ", 40.74, -74.17, 2.0),
    ("New York", "NY", 40.71, -74.01, 16.0),
    ("Hartford", "CT", 41.76, -72.68, 1.2),
    ("Providence", "RI", 41.82, -71.41, 1.7),
    ("Boston", "MA", 42.36, -71.06, 4.9),
    ("Albany", "NY", 42.65, -73.75, 0.9),
    ("Buffalo", "NY", 42.89, -78.88, 1.1),
    ("Manchester", "NH", 42.99, -71.46, 0.4),
    ("Portland", "ME", 43.66, -70.26, 0.55),
    ("Burlington", "VT", 44.48, -73.21, 0.2),
    ("Charleston", "WV", 38.35, -81.63, 0.2),
    ("Anchorage", "AK", 61.22, -149.90, 0.4),
    ("Honolulu", "HI", 21.31, -157.86, 1.0),
]

# A few real ZIP codes in or near each city's center, used for generated customer addresses.
# Strings, not ints: several start with 0 (e.g. Boston 02108).
CITY_ZIPS: dict[tuple[str, str], tuple[str, ...]] = {
    ("Seattle", "WA"): ("98101", "98104", "98109"),
    ("Spokane", "WA"): ("99201", "99202", "99205"),
    ("Portland", "OR"): ("97201", "97205", "97209"),
    ("San Francisco", "CA"): ("94102", "94103", "94110"),
    ("San Jose", "CA"): ("95110", "95112", "95113"),
    ("Sacramento", "CA"): ("95814", "95816", "95818"),
    ("Fresno", "CA"): ("93701", "93704", "93721"),
    ("Los Angeles", "CA"): ("90012", "90015", "90017"),
    ("San Diego", "CA"): ("92101", "92103", "92104"),
    ("Las Vegas", "NV"): ("89101", "89104", "89106"),
    ("Reno", "NV"): ("89501", "89502", "89503"),
    ("Phoenix", "AZ"): ("85003", "85004", "85006"),
    ("Tucson", "AZ"): ("85701", "85705", "85719"),
    ("Salt Lake City", "UT"): ("84101", "84102", "84111"),
    ("Boise", "ID"): ("83702", "83704", "83706"),
    ("Denver", "CO"): ("80202", "80203", "80205"),
    ("Albuquerque", "NM"): ("87102", "87106", "87108"),
    ("El Paso", "TX"): ("79901", "79902", "79903"),
    ("Billings", "MT"): ("59101", "59102", "59105"),
    ("Cheyenne", "WY"): ("82001", "82007", "82009"),
    ("Dallas", "TX"): ("75201", "75202", "75204"),
    ("Houston", "TX"): ("77002", "77003", "77006"),
    ("Austin", "TX"): ("78701", "78702", "78704"),
    ("San Antonio", "TX"): ("78204", "78205", "78210"),
    ("Oklahoma City", "OK"): ("73102", "73103", "73104"),
    ("Wichita", "KS"): ("67202", "67203", "67211"),
    ("Kansas City", "MO"): ("64105", "64106", "64108"),
    ("St. Louis", "MO"): ("63101", "63102", "63103"),
    ("Omaha", "NE"): ("68102", "68105", "68131"),
    ("Des Moines", "IA"): ("50309", "50312", "50314"),
    ("Minneapolis", "MN"): ("55401", "55402", "55403"),
    ("Fargo", "ND"): ("58102", "58103", "58104"),
    ("Sioux Falls", "SD"): ("57103", "57104", "57105"),
    ("Milwaukee", "WI"): ("53202", "53203", "53204"),
    ("Chicago", "IL"): ("60601", "60602", "60606"),
    ("Indianapolis", "IN"): ("46202", "46204", "46225"),
    ("Detroit", "MI"): ("48201", "48207", "48226"),
    ("Grand Rapids", "MI"): ("49503", "49504", "49506"),
    ("Columbus", "OH"): ("43201", "43206", "43215"),
    ("Cleveland", "OH"): ("44113", "44114", "44115"),
    ("Cincinnati", "OH"): ("45202", "45203", "45206"),
    ("Pittsburgh", "PA"): ("15212", "15219", "15222"),
    ("Louisville", "KY"): ("40202", "40203", "40204"),
    ("Nashville", "TN"): ("37201", "37203", "37206"),
    ("Memphis", "TN"): ("38103", "38104", "38105"),
    ("Little Rock", "AR"): ("72201", "72202", "72205"),
    ("New Orleans", "LA"): ("70112", "70113", "70130"),
    ("Jackson", "MS"): ("39201", "39202", "39203"),
    ("Birmingham", "AL"): ("35203", "35205", "35233"),
    ("Atlanta", "GA"): ("30303", "30308", "30309"),
    ("Charlotte", "NC"): ("28202", "28203", "28204"),
    ("Raleigh", "NC"): ("27601", "27603", "27605"),
    ("Charleston", "SC"): ("29401", "29403", "29407"),
    ("Jacksonville", "FL"): ("32202", "32204", "32206"),
    ("Orlando", "FL"): ("32801", "32803", "32806"),
    ("Tampa", "FL"): ("33602", "33606", "33609"),
    ("Miami", "FL"): ("33128", "33130", "33131"),
    ("Richmond", "VA"): ("23219", "23220", "23223"),
    ("Washington", "DC"): ("20001", "20005", "20009"),
    ("Baltimore", "MD"): ("21201", "21202", "21230"),
    ("Wilmington", "DE"): ("19801", "19802", "19806"),
    ("Philadelphia", "PA"): ("19102", "19103", "19107"),
    ("Newark", "NJ"): ("07102", "07103", "07105"),
    ("New York", "NY"): ("10001", "10007", "10016"),
    ("Hartford", "CT"): ("06103", "06105", "06106"),
    ("Providence", "RI"): ("02903", "02906", "02908"),
    ("Boston", "MA"): ("02108", "02110", "02116"),
    ("Albany", "NY"): ("12203", "12207", "12210"),
    ("Buffalo", "NY"): ("14201", "14202", "14203"),
    ("Manchester", "NH"): ("03101", "03102", "03104"),
    ("Portland", "ME"): ("04101", "04102", "04103"),
    ("Burlington", "VT"): ("05401", "05405", "05408"),
    ("Charleston", "WV"): ("25301", "25302", "25311"),
    ("Anchorage", "AK"): ("99501", "99503", "99508"),
    ("Honolulu", "HI"): ("96813", "96814", "96815"),
}

# Approximate geographic center of each state (fallback when a city is unknown).
STATE_CENTROIDS: dict[str, tuple[float, float]] = {
    "AL": (32.8, -86.8), "AK": (61.4, -150.0), "AZ": (34.2, -111.7), "AR": (34.9, -92.4),
    "CA": (37.2, -119.5), "CO": (39.0, -105.5), "CT": (41.6, -72.7), "DE": (39.0, -75.5),
    "DC": (38.9, -77.0), "FL": (28.6, -82.4), "GA": (32.7, -83.4), "HI": (20.8, -156.3),
    "ID": (44.4, -114.6), "IL": (40.0, -89.2), "IN": (39.9, -86.3), "IA": (42.1, -93.5),
    "KS": (38.5, -98.4), "KY": (37.5, -85.3), "LA": (31.1, -92.0), "ME": (45.4, -69.2),
    "MD": (39.0, -76.8), "MA": (42.3, -71.8), "MI": (44.3, -85.4), "MN": (46.3, -94.3),
    "MS": (32.7, -89.7), "MO": (38.4, -92.5), "MT": (47.0, -109.6), "NE": (41.5, -99.8),
    "NV": (39.3, -116.6), "NH": (43.7, -71.6), "NJ": (40.2, -74.7), "NM": (34.4, -106.1),
    "NY": (42.9, -75.5), "NC": (35.6, -79.4), "ND": (47.5, -100.5), "OH": (40.3, -82.8),
    "OK": (35.6, -97.5), "OR": (43.9, -120.6), "PA": (40.9, -77.8), "RI": (41.7, -71.5),
    "SC": (33.9, -80.9), "SD": (44.4, -100.2), "TN": (35.9, -86.4), "TX": (31.5, -99.3),
    "UT": (39.3, -111.7), "VT": (44.1, -72.7), "VA": (37.5, -78.9), "WA": (47.4, -120.5),
    "WV": (38.6, -80.6), "WI": (44.6, -89.9), "WY": (43.0, -107.6),
}

EARTH_RADIUS_MILES = 3958.8


def _key(city: str, state: str) -> tuple[str, str]:
    return (" ".join(city.split()).lower(), state.strip().upper())


class Geo:
    """City/state → coordinates, with a state-centroid fallback."""

    def __init__(self, cities: Iterable[tuple[str, str, float, float, float]] = CITIES) -> None:
        self._coords: dict[tuple[str, str], tuple[float, float]] = {}
        for city, state, lat, lon, _w in cities:
            self._coords[_key(city, state)] = (lat, lon)

    def register_city(self, city: str, state: str, lat: float, lon: float) -> None:
        self._coords[_key(city, state)] = (lat, lon)

    def knows_city(self, city: str, state: str) -> bool:
        return _key(city, state) in self._coords

    def coords(self, city: str, state: str) -> tuple[float, float]:
        key = _key(city, state)
        if key in self._coords:
            return self._coords[key]
        if key[1] in STATE_CENTROIDS:
            return STATE_CENTROIDS[key[1]]
        raise ValueError(f"unknown location {city!r}, {state!r}: state is not a US state code")


def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(h))


def nearest_center_id(city: str, state: str, centers: Iterable, geo: "Geo | None" = None) -> str:
    """Id of the center closest to (city, state), by great-circle distance between cities.

    ``centers`` are anything with ``.id`` and ``.address`` (e.g. ``obs.centers``); ties go
    to the first one listed.
    """
    from .models import Address

    geo = geo or Geo()
    here = geo.coords(city, state)
    best_id, best_dist = None, math.inf
    for c in centers:
        a = Address.parse(c.address)
        d = haversine_miles(here, geo.coords(a.city, a.state))
        if d < best_dist:
            best_id, best_dist = c.id, d
    if best_id is None:
        raise RuntimeError("no delivery centers are open")
    return best_id


class CenterResolver(Protocol):
    """Decides which delivery center serves a customer address."""

    def resolve(self, address: "Address", centers: Iterable["DeliveryCenter"]) -> str: ...


class NearestCenterResolver:
    """Closest open delivery center by great-circle distance."""

    def __init__(self, geo: Geo) -> None:
        self.geo = geo

    def resolve(self, address: "Address", centers: Iterable["DeliveryCenter"]) -> str:
        here = self.geo.coords(address.city, address.state)
        best_id, best_dist = None, math.inf
        for c in centers:
            d = haversine_miles(here, (c.lat, c.lon))
            if d < best_dist:
                best_id, best_dist = c.id, d
        if best_id is None:
            raise RuntimeError("no delivery centers are open")
        return best_id
