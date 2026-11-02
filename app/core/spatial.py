"""Spatial queries shared by everything placed on the map: pickup requests, recyclers, ECAs and, later, routes.

Points are stored as `geography(Point, 4326)`, so distances and radii are meters on the Earth's spheroid and
nobody converts degrees by hand. Each helper keeps the column bare on its side of the expression so that
PostgreSQL answers it from the column's GiST index (`ST_DWithin` and the `<->` operator use it; wrapping the
column in a function or a cast would not).
"""
from decimal import Decimal
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import ColumnElement, Float, cast, func
from sqlalchemy.orm import QueryableAttribute

# The pilot has no street network: a road trip is estimated as the straight line times this factor.
# `road_distance_meters` is the one place to swap for pgRouting or OSRM when the street graph exists.
ROAD_DISTANCE_FACTOR = 1.4

SpatialExpression = ColumnElement[Any] | QueryableAttribute[Any]


def point(latitude: float | Decimal, longitude: float | Decimal) -> ColumnElement[Any]:
    """A WGS 84 geography point. Usable as a value to store and as the other side of any helper below."""
    return cast(func.ST_SetSRID(func.ST_MakePoint(longitude, latitude), 4326),
                Geography(geometry_type="POINT", srid=4326))


def within_radius(location: SpatialExpression, center: SpatialExpression, meters: float) -> ColumnElement[bool]:
    """True when `location` is at most `meters` from `center`. Index-backed; use it to filter."""
    return func.ST_DWithin(location, center, meters)


def nearest_first(location: SpatialExpression, center: SpatialExpression) -> ColumnElement[float]:
    """An ORDER BY key, closest to `center` first, answered by walking the index (KNN).

    Add the row's `id` after it as a tie-breaker when the listing is paginated.
    """
    return location.op("<->", return_type=Float)(center)


def distance_meters(location: SpatialExpression, other: SpatialExpression) -> ColumnElement[float]:
    """Straight-line distance in meters on the spheroid."""
    return func.ST_Distance(location, other, type_=Float)


def road_distance_meters(location: SpatialExpression, other: SpatialExpression) -> ColumnElement[float]:
    """Estimated distance by road, in meters (see ROAD_DISTANCE_FACTOR)."""
    return distance_meters(location, other) * ROAD_DISTANCE_FACTOR
