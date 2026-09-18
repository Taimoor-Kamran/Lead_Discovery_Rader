"""A search job's geo must be a complete, usable area."""

import pytest
from pydantic import ValidationError

from app.modules.jobs.schemas import GeoSpec, SearchJobCreate


def test_city_and_state_is_accepted() -> None:
    assert GeoSpec(city="Austin", state="TX").city == "Austin"


def test_point_and_radius_is_accepted() -> None:
    geo = GeoSpec(lat=30.26, lng=-97.74, radius_m=5000)
    assert geo.radius_m == 5000


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"city": "Austin"},
        {"state": "TX"},
        {"lat": 30.26, "lng": -97.74},
        {"lat": 30.26, "radius_m": 5000},
    ],
)
def test_incomplete_geo_is_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GeoSpec(**payload)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "payload",
    [
        {"lat": 200.0, "lng": 0.0, "radius_m": 100},
        {"lat": 0.0, "lng": 400.0, "radius_m": 100},
        {"lat": 0.0, "lng": 0.0, "radius_m": 0},
    ],
)
def test_out_of_range_coordinates_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GeoSpec(**payload)  # type: ignore[arg-type]


def test_unknown_geo_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        GeoSpec(city="Austin", state="TX", country="US")  # type: ignore[call-arg]


def test_search_job_requires_a_valid_geo() -> None:
    with pytest.raises(ValidationError):
        SearchJobCreate(name="n", geo={"city": "Austin"}, industry="dental")  # type: ignore[arg-type]
