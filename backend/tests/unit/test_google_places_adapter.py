"""The Google Places adapter, driven entirely from recorded responses.

Everything here answers one question: does a Places response become exactly the records
it describes — no more, no fewer, and with nothing invented along the way?
"""

import json
from typing import Any

import fakeredis
import httpx
import pytest
import respx

from app.modules.adapters.base import Candidate, DiscoveryConfig, RawDoc, Ref
from app.modules.adapters.errors import AdapterError, AuthError, SchemaError
from app.modules.adapters.google_places.adapter import (
    MAX_RESULTS_PER_QUERY,
    GooglePlacesAdapter,
    build_query,
    run_call_ceiling,
)
from app.modules.adapters.google_places.client import PLACES_BASE_URL, TEXT_SEARCH_PATH
from app.modules.jobs.schemas import GeoSpec
from tests.conftest import FakeClock, build_places_adapter, places_fixture

SEARCH_URL = f"{PLACES_BASE_URL}{TEXT_SEARCH_PATH}"
JOB_RUN_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def adapter(fake_redis: fakeredis.FakeStrictRedis, clock: FakeClock) -> GooglePlacesAdapter:
    """A Places adapter with no database behind it; metering is exercised elsewhere."""
    return build_places_adapter(fake_redis, clock, meter=lambda record: None)


def sent_body(route: respx.Route, index: int) -> dict[str, Any]:
    """The JSON body of the nth request the adapter actually sent."""
    body: dict[str, Any] = json.loads(route.calls[index].request.read())
    return body


def config(**overrides: Any) -> DiscoveryConfig:
    values: dict[str, Any] = {
        "industry": "plumber",
        "geo": GeoSpec(city="Austin", state="TX"),
        "job_run_id": JOB_RUN_ID,
        "max_results": 60,
    }
    values.update(overrides)
    return DiscoveryConfig(**values)


def three_pages(mock_http: respx.MockRouter) -> respx.Route:
    return mock_http.post(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=places_fixture("text_search_page_1.json")),
            httpx.Response(200, json=places_fixture("text_search_page_2.json")),
            httpx.Response(200, json=places_fixture("text_search_page_3.json")),
        ]
    )


# --- query building ----------------------------------------------------------------


def test_a_city_and_state_become_a_plain_text_query() -> None:
    query, bias = build_query("plumber", GeoSpec(city="Austin", state="TX"))

    assert query == "plumber in Austin, TX"
    assert bias is None


def test_a_point_and_radius_become_a_location_bias() -> None:
    query, bias = build_query("plumber", GeoSpec(lat=30.26, lng=-97.74, radius_m=5000))

    assert query == "plumber"
    assert bias == {
        "circle": {"center": {"latitude": 30.26, "longitude": -97.74}, "radius": 5000.0}
    }


def test_a_city_and_state_win_when_both_shapes_are_present() -> None:
    geo = GeoSpec(city="Austin", state="TX", lat=30.26, lng=-97.74, radius_m=5000)

    query, bias = build_query("plumber", geo)

    assert query == "plumber in Austin, TX"
    assert bias is None


def test_an_unusable_area_is_refused_before_any_call_is_made() -> None:
    geo = GeoSpec.model_construct(city=None, state=None, lat=None, lng=None, radius_m=None)

    with pytest.raises(AdapterError, match="city and state"):
        build_query("plumber", geo)


# --- pagination and caps -----------------------------------------------------------


def test_three_pages_yield_sixty_refs_from_three_calls(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    route = three_pages(mock_http)

    refs = list(adapter.discover(config()))

    assert len(refs) == 60
    assert route.call_count == 3
    assert len({ref.source_record_id for ref in refs}) == 60


def test_pagination_passes_the_token_from_the_previous_page(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    route = three_pages(mock_http)

    list(adapter.discover(config()))

    tokens = [sent_body(route, i).get("pageToken") for i in range(3)]
    assert tokens == [None, "radar-page-token-2", "radar-page-token-3"]


def test_a_lower_max_results_stops_part_way_through_the_second_page(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    route = three_pages(mock_http)

    refs = list(adapter.discover(config(max_results=25)))

    assert len(refs) == 25
    assert route.call_count == 2, "the third page is never paid for"


def test_every_page_asks_for_a_full_twenty_and_the_last_is_trimmed_here(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    """Asking for fewer near the limit made Places answer short and then empty pages."""
    route = three_pages(mock_http)

    refs = list(adapter.discover(config(max_results=25)))

    assert [sent_body(route, i)["pageSize"] for i in range(2)] == [20, 20]
    assert len(refs) == 25


def test_a_request_for_more_than_the_api_can_give_is_clamped(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    three_pages(mock_http)

    refs = list(adapter.discover(config(max_results=5_000)))

    assert len(refs) == MAX_RESULTS_PER_QUERY == 60


def test_a_max_of_zero_makes_no_call_at_all(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    route = mock_http.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={}))

    assert list(adapter.discover(config(max_results=0))) == []
    assert route.call_count == 0


def test_an_empty_result_yields_nothing_and_stops_after_one_call(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    route = mock_http.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=places_fixture("text_search_empty.json"))
    )

    assert list(adapter.discover(config())) == []
    assert route.call_count == 1


def test_a_page_without_a_token_ends_the_walk(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    route = mock_http.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=places_fixture("text_search_page_3.json"))
    )

    refs = list(adapter.discover(config()))

    assert len(refs) == 20
    assert route.call_count == 1


def test_cancellation_is_checked_before_every_page(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    three_pages(mock_http)
    checks: list[int] = []

    list(adapter.discover(config(cancel_check=lambda: checks.append(1))))

    assert len(checks) == 3


def test_a_cancellation_stops_the_walk_where_it_is(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    route = three_pages(mock_http)
    seen = 0

    def stop_after_first_page() -> None:
        nonlocal seen
        seen += 1
        if seen > 1:
            raise KeyboardInterrupt("cancelled")

    with pytest.raises(KeyboardInterrupt):
        list(adapter.discover(config(cancel_check=stop_after_first_page)))

    assert route.call_count == 1


# --- v0.11.0: a walk that never ends ----------------------------------------------------
#
# Before v0.11.0, a 52-result search paged until the daily cap stopped it: 500 calls over
# four runs. The live walk that explained it (2026-09-24, "electrician in Austin, TX",
# asking for only what was still wanted) got pages of 19, 16, 12, 4 and 1 places, then a
# page with no places and a fresh token — recorded verbatim in
# `text_search_empty_page_with_token_recorded.json`. Following that token never ends.


def pool_of_places() -> list[dict[str, Any]]:
    """Sixty distinct places from the three constructed pages."""
    return [
        place
        for name in (
            "text_search_page_1.json",
            "text_search_page_2.json",
            "text_search_page_3.json",
        )
        for place in places_fixture(name)["places"]
    ]


def short_pages(*sizes: int, last_has_token: bool = True) -> list[httpx.Response]:
    """Pages of distinct places in the given sizes, each carrying a token to the next."""
    pool = pool_of_places()
    pages: list[httpx.Response] = []
    start = 0
    for number, size in enumerate(sizes, start=1):
        body: dict[str, Any] = {"places": pool[start : start + size]}
        if number < len(sizes) or last_has_token:
            body["nextPageToken"] = f"radar-short-page-token-{number + 1}"
        pages.append(httpx.Response(200, json=body))
        start += size
    return pages


def then_forever(pages: list[httpx.Response], repeat: dict[str, Any]) -> Any:
    """A side effect that serves `pages` in order and then `repeat` for every later call."""
    served = iter(pages)

    def respond(request: httpx.Request) -> httpx.Response:
        return next(served, None) or httpx.Response(200, json=repeat)

    return respond


def test_the_recorded_empty_page_ends_the_walk_that_once_cost_five_hundred_calls(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    empty = places_fixture("text_search_empty_page_with_token_recorded.json")
    assert empty.keys() == {"nextPageToken"}, "the recording is a token and nothing else"
    route = mock_http.post(SEARCH_URL).mock(
        side_effect=then_forever(short_pages(19, 16, 12, 4, 1), empty)
    )

    refs = list(adapter.discover(config(max_results=60)))

    assert len(refs) == 52
    assert route.call_count == 6, "five pages with places, one empty page, then stop"


def test_an_empty_first_page_with_a_token_costs_one_call(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    route = mock_http.post(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json=places_fixture("text_search_empty_page_with_token_recorded.json")
        )
    )

    assert list(adapter.discover(config())) == []
    assert route.call_count == 1


def test_a_page_of_places_already_seen_ends_the_walk(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    """Repeats stall the walk exactly as an empty page does, and cost the same."""
    first = places_fixture("text_search_page_1.json")
    route = mock_http.post(SEARCH_URL).mock(side_effect=then_forever([], first))

    refs = list(adapter.discover(config()))

    assert len(refs) == 20
    assert route.call_count == 2, "the second page brought nothing new"


def test_a_place_repeated_within_the_walk_is_yielded_once(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    pool = pool_of_places()
    mock_http.post(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json={"places": pool[:10], "nextPageToken": "t2"}),
            httpx.Response(200, json={"places": pool[5:15]}),
        ]
    )

    refs = list(adapter.discover(config()))

    ids = [ref.source_record_id for ref in refs]
    assert len(ids) == len(set(ids)) == 15


def test_short_pages_do_not_end_the_walk_early(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    """The first live walk: 60 places took five calls, not three."""
    route = mock_http.post(SEARCH_URL).mock(
        side_effect=short_pages(20, 16, 12, 10, 2, last_has_token=False)
    )

    refs = list(adapter.discover(config(max_results=60)))

    assert len(refs) == 60
    assert route.call_count == 5


def test_the_client_is_built_with_the_runs_safety_ceiling(
    fake_redis: fakeredis.FakeStrictRedis, clock: FakeClock, mock_http: respx.MockRouter
) -> None:
    from app.modules.adapters.google_places.client import build_client

    ceilings: list[int] = []

    def factory(job_run_id: Any, max_calls: int) -> Any:
        ceilings.append(max_calls)
        return build_client(
            GooglePlacesAdapter.name,
            job_run_id=job_run_id,
            max_calls=max_calls,
            sleeper=clock.sleep,
            clock=clock,
            redis_client=fake_redis,
            meter=lambda record: None,
        )

    three_pages(mock_http)
    list(GooglePlacesAdapter(client_factory=factory).discover(config(max_results=60)))

    assert ceilings == [run_call_ceiling(60)] == [12]


@pytest.mark.parametrize(
    ("limit", "ceiling"), [(0, 0), (1, 4), (20, 4), (21, 8), (45, 12), (60, 12), (200, 40)]
)
def test_the_ceiling_is_the_multiplier_times_the_fewest_possible_pages(
    limit: int, ceiling: int
) -> None:
    """It keeps scaling with the search, so it holds for sizes nobody has tried yet."""
    assert run_call_ceiling(limit) == ceiling


# --- headers and the key -----------------------------------------------------------


def test_the_key_and_the_field_mask_travel_in_headers_not_the_url(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    from tests.conftest import TEST_PLACES_API_KEY

    route = mock_http.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={}))

    list(adapter.discover(config()))

    request = route.calls[0].request
    assert request.headers["X-Goog-Api-Key"] == TEST_PLACES_API_KEY
    assert "places.id" in request.headers["X-Goog-FieldMask"]
    assert "nextPageToken" in request.headers["X-Goog-FieldMask"]
    assert TEST_PLACES_API_KEY not in str(request.url)


# --- error mapping -----------------------------------------------------------------


def test_a_403_becomes_an_auth_error_that_names_what_to_check(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    route = mock_http.post(SEARCH_URL).mock(
        return_value=httpx.Response(403, json=places_fixture("error_403.json"))
    )

    with pytest.raises(AuthError) as caught:
        list(adapter.discover(config()))

    assert route.call_count == 1, "an auth failure is never retried"
    assert "GOOGLE_PLACES_API_KEY" in str(caught.value)


def test_a_body_that_breaks_the_contract_becomes_a_schema_error(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    route = mock_http.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=places_fixture("malformed_body.json"))
    )

    with pytest.raises(SchemaError):
        list(adapter.discover(config()))

    assert route.call_count == 2, "one retry, in case it was a blip"


# --- fetch, validate, normalize ----------------------------------------------------


def test_fetch_wraps_what_discovery_already_saw_without_a_second_call(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    route = three_pages(mock_http)
    ref = next(iter(adapter.discover(config(max_results=1))))

    raw = adapter.fetch(ref)

    assert route.call_count == 1
    assert raw.source == "google_places"
    assert raw.source_record_id == ref.source_record_id
    assert raw.payload == ref.payload
    assert raw.fetched_at.tzinfo is not None


def test_a_ref_without_a_payload_cannot_be_fetched(adapter: GooglePlacesAdapter) -> None:
    with pytest.raises(AdapterError, match="always carry their payload"):
        adapter.fetch(Ref(source_record_id="ChIJnothing"))


def test_the_source_url_points_at_the_place_id(
    mock_http: respx.MockRouter, adapter: GooglePlacesAdapter
) -> None:
    three_pages(mock_http)

    ref = next(iter(adapter.discover(config(max_results=1))))

    assert ref.source_url == (
        f"https://www.google.com/maps/place/?q=place_id:{ref.source_record_id}"
    )


def raw_doc(payload: dict[str, Any]) -> RawDoc:
    from datetime import UTC, datetime

    return RawDoc(
        source="google_places",
        source_record_id=str(payload.get("id", "")),
        source_url=None,
        payload=payload,
        fetched_at=datetime.now(UTC),
    )


def test_a_place_without_an_id_is_invalid(adapter: GooglePlacesAdapter) -> None:
    payload = places_fixture("text_search_with_invalid_place.json")["places"][1]

    result = adapter.validate(raw_doc(payload))

    assert result.valid is False
    assert any("id" in error for error in result.errors)


def test_a_place_with_nothing_but_an_id_is_still_valid(adapter: GooglePlacesAdapter) -> None:
    result = adapter.validate(raw_doc({"id": "ChIJradar0059MinimalPlace"}))

    assert result.valid is True
    assert result.errors == []


def test_an_unexpected_new_field_does_not_invalidate_a_place(
    adapter: GooglePlacesAdapter,
) -> None:
    result = adapter.validate(raw_doc({"id": "ChIJx", "somethingGoogleAddedLastWeek": {"a": 1}}))

    assert result.valid is True


def test_normalize_copies_every_mapped_field(adapter: GooglePlacesAdapter) -> None:
    payload = places_fixture("text_search_page_1.json")["places"][0]

    candidate = adapter.normalize(raw_doc(payload))

    assert candidate.display_name == payload["displayName"]["text"]
    assert candidate.formatted_address == payload["formattedAddress"]
    assert candidate.phone == payload["nationalPhoneNumber"]
    assert candidate.website == payload["websiteUri"]
    assert candidate.business_status == "OPERATIONAL"
    assert candidate.types == payload["types"]
    assert candidate.lat == payload["location"]["latitude"]
    assert candidate.lng == payload["location"]["longitude"]


def test_missing_optional_fields_stay_null_and_are_never_guessed(
    adapter: GooglePlacesAdapter,
) -> None:
    """A place that carries only an ID must produce an all-null candidate."""
    candidate = adapter.normalize(raw_doc({"id": "ChIJradar0059MinimalPlace"}))

    assert candidate == Candidate()
    assert candidate.display_name is None
    assert candidate.formatted_address is None
    assert candidate.phone is None
    assert candidate.website is None
    assert candidate.business_status is None
    assert candidate.types is None
    assert candidate.lat is None and candidate.lng is None


def test_the_international_number_is_used_only_when_there_is_no_national_one(
    adapter: GooglePlacesAdapter,
) -> None:
    candidate = adapter.normalize(
        raw_doc({"id": "ChIJx", "internationalPhoneNumber": "+1 512-555-0000"})
    )

    assert candidate.phone == "+1 512-555-0000"


def test_places_emits_no_events_in_the_mvp(adapter: GooglePlacesAdapter) -> None:
    assert adapter.emit_events(raw_doc({"id": "ChIJx"})) == []


def test_the_rate_limit_and_metadata_come_from_settings(adapter: GooglePlacesAdapter) -> None:
    limits = adapter.get_rate_limit()
    meta = adapter.get_source_metadata()

    assert limits.requests_per_second == 5.0
    assert limits.daily_call_cap == 200
    assert meta.content_ttl_days == 30
    assert meta.terms_url.startswith("https://")


# --- v0.11.0: rating and review count ------------------------------------------------------


def test_the_default_field_mask_asks_for_rating_and_review_count_and_nothing_richer() -> None:
    from app.core.config import DEFAULT_PLACES_FIELD_MASK

    fields = DEFAULT_PLACES_FIELD_MASK.split(",")

    assert "places.rating" in fields
    assert "places.userRatingCount" in fields
    # Enterprise + Atmosphere, and review text carries attribution duties: never requested.
    assert not {"places.reviews", "places.editorialSummary"} & set(fields)


def test_the_default_field_mask_asks_for_the_providers_that_must_be_shown() -> None:
    """v0.11.1: `attributions` are data providers Google requires shown with the result."""
    from app.core.config import DEFAULT_PLACES_FIELD_MASK

    assert "places.attributions" in DEFAULT_PLACES_FIELD_MASK.split(",")


def test_rating_and_review_count_are_copied_verbatim(adapter: GooglePlacesAdapter) -> None:
    candidate = adapter.normalize(raw_doc({"id": "place-1", "rating": 4.6, "userRatingCount": 11}))

    assert (candidate.rating, candidate.user_rating_count) == (4.6, 11)


def test_a_place_without_a_rating_leaves_both_null(adapter: GooglePlacesAdapter) -> None:
    candidate = adapter.normalize(raw_doc({"id": "place-2"}))

    assert (candidate.rating, candidate.user_rating_count) == (None, None)
