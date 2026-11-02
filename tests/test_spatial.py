"""Spatial queries (Sprint 6): radius, nearest first and distances over geography points, answered by the
GiST indexes, checked with places in Usme (Bogotá).

Coordinates are approximate; expected distances come from the haversine formula, which differs from
PostGIS's spheroid by well under 1% at this scale.
"""
import math
from decimal import Decimal

import pytest
from sqlalchemy import Select, select, text
from sqlalchemy.dialects import postgresql

from app.core.spatial import (
    ROAD_DISTANCE_FACTOR,
    distance_meters,
    nearest_first,
    point,
    road_distance_meters,
    within_radius,
)
from app.domains.collections.models import Collection, CollectionStatus
from app.domains.organizations.models import Organization
from app.domains.users.models import User
from tests import factories

PORTAL_USME = (4.5316, -74.1176)
SANTA_LIBRADA = (4.5353, -74.1129)   # ~0.7 km from the portal
ALFONSO_LOPEZ = (4.5100, -74.1050)   # ~2.8 km
USME_PUEBLO = (4.4766, -74.1262)     # ~6.2 km


def _haversine_meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_008.8 * math.asin(math.sqrt(h))


def _collection(db, requester, place: tuple[float, float], address: str) -> Collection:
    latitude, longitude = (Decimal(str(value)) for value in place)
    collection = Collection(requester_id=requester.id, status=CollectionStatus.pending_assignment, address=address,
                            latitude=latitude, longitude=longitude, location=point(latitude, longitude),
                            materials=["paper"])
    db.add(collection)
    db.commit()
    return collection


@pytest.fixture
def usme_collections(db):
    citizen = factories.make_user(db, "citizen")
    return {name: _collection(db, citizen, place, name)
            for name, place in {"santa_librada": SANTA_LIBRADA, "alfonso_lopez": ALFONSO_LOPEZ,
                                "usme_pueblo": USME_PUEBLO}.items()}


def _addresses(db, statement: Select) -> list[str]:
    return list(db.scalars(statement))


class TestRadius:
    def test_only_points_inside_the_radius_are_returned(self, db, usme_collections):
        center = point(*PORTAL_USME)
        assert _addresses(db, select(Collection.address)
                          .where(within_radius(Collection.location, center, 1_000))) == ["santa_librada"]
        assert sorted(_addresses(db, select(Collection.address)
                                 .where(within_radius(Collection.location, center, 3_000)))) == [
            "alfonso_lopez", "santa_librada"]

    def test_the_radius_is_in_meters_not_degrees(self, db, usme_collections):
        # 0.05 read as degrees would reach every point; as meters it reaches none.
        assert _addresses(db, select(Collection.address)
                          .where(within_radius(Collection.location, point(*PORTAL_USME), 0.05))) == []


class TestNearest:
    def test_points_come_closest_first(self, db, usme_collections):
        statement = (select(Collection.address)
                     .order_by(nearest_first(Collection.location, point(*PORTAL_USME)), Collection.id))
        assert _addresses(db, statement) == ["santa_librada", "alfonso_lopez", "usme_pueblo"]

    def test_the_closest_one_from_somewhere_else(self, db, usme_collections):
        statement = (select(Collection.address)
                     .order_by(nearest_first(Collection.location, point(*USME_PUEBLO)), Collection.id).limit(1))
        assert _addresses(db, statement) == ["usme_pueblo"]


class TestDistance:
    @pytest.mark.parametrize("place", [SANTA_LIBRADA, ALFONSO_LOPEZ, USME_PUEBLO])
    def test_straight_line_distance_in_meters(self, db, place):
        measured = db.scalar(select(distance_meters(point(*PORTAL_USME), point(*place))))
        assert measured == pytest.approx(_haversine_meters(PORTAL_USME, place), rel=0.01)

    def test_between_two_stored_points(self, db, usme_collections):
        recycler = factories.make_user(db, "recycler", start_location=point(*PORTAL_USME))
        start = select(User.start_location).where(User.id == recycler.id).scalar_subquery()
        pickup = select(Collection.location).where(Collection.address == "usme_pueblo").scalar_subquery()
        measured = db.scalar(select(distance_meters(start, pickup)))
        assert measured == pytest.approx(_haversine_meters(PORTAL_USME, USME_PUEBLO), rel=0.01)

    def test_road_distance_is_the_straight_line_times_the_pilot_factor(self, db):
        straight = db.scalar(select(distance_meters(point(*PORTAL_USME), point(*USME_PUEBLO))))
        by_road = db.scalar(select(road_distance_meters(point(*PORTAL_USME), point(*USME_PUEBLO))))
        assert ROAD_DISTANCE_FACTOR == 1.4
        assert by_road == pytest.approx(straight * 1.4)


class TestRecyclersAndEcas:
    def test_the_nearest_recycler_to_a_pickup(self, db):
        near = factories.make_user(db, "recycler", start_location=point(*SANTA_LIBRADA))
        far = factories.make_user(db, "recycler", start_location=point(*USME_PUEBLO))
        statement = (select(User.id).where(User.id.in_([far.id, near.id]))
                     .order_by(nearest_first(User.start_location, point(*PORTAL_USME)), User.id))
        assert list(db.scalars(statement)) == [near.id, far.id]

    def test_ecas_within_reach_of_a_point(self, db):
        close = factories.make_organization(db, "eca", location=point(*ALFONSO_LOPEZ))
        distant = factories.make_organization(db, "eca", location=point(*USME_PUEBLO))
        found = set(db.scalars(select(Organization.id)
                               .where(within_radius(Organization.location, point(*PORTAL_USME), 3_000))))
        assert close.id in found and distant.id not in found


def _plan(db, statement: Select) -> str:
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    return "\n".join(db.scalars(text(f"EXPLAIN {sql}")))


INDEXED_POINTS = [
    pytest.param(Collection.location, Collection.id, "ix_collections_location", id="collections"),
    pytest.param(User.start_location, User.id, "ix_users_start_location", id="recyclers"),
    pytest.param(Organization.location, Organization.id, "ix_organizations_location", id="ecas"),
]


class TestIndexes:
    """The planner can answer each query from the GiST index. Sequential scans are switched off because on the
    handful of test rows a full scan would be cheaper and hide whether the index is usable at all."""

    @pytest.fixture(autouse=True)
    def _prefer_indexes(self, db):
        db.execute(text("SET LOCAL enable_seqscan = off"))

    @pytest.mark.parametrize(("location", "row_id", "index"), INDEXED_POINTS)
    def test_radius_uses_the_index(self, db, location, row_id, index):
        assert index in _plan(db, select(row_id).where(within_radius(location, point(*PORTAL_USME), 1_000)))

    @pytest.mark.parametrize(("location", "row_id", "index"), INDEXED_POINTS)
    def test_nearest_uses_the_index(self, db, location, row_id, index):
        assert index in _plan(db, select(row_id).order_by(nearest_first(location, point(*PORTAL_USME))).limit(5))
