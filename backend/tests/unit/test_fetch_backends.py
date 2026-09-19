"""The offline fetch backend: which hosts it answers for, and what it answers with.

It is the reason the whole audit engine is testable and demoable with no network at all,
so the rule about *which* hosts it may answer for is asserted here rather than assumed.
"""

import json
import os
from typing import Any

import pytest

from app.core.config import Settings
from app.core.fetch_backends import (
    ConnectFailedError,
    FetchRequest,
    FetchTimeoutError,
    FixtureFetchBackend,
    NetworkFetchBackend,
    TlsVerificationError,
    default_backends,
    demo_site_dir,
    demo_sites_root,
)
from app.core.safe_fetch import split_safe_url


def request(url: str, *, max_bytes: int = 2_000_000) -> FetchRequest:
    return FetchRequest(
        url=url,
        parts=split_safe_url(url),
        headers={},
        connect_timeout=1.0,
        read_timeout=1.0,
        max_bytes=max_bytes,
    )


@pytest.fixture
def site(tmp_path: Any) -> Any:
    """A demo site fixture directory with a homepage and an open robots.txt."""
    directory = tmp_path / "demo.invalid"
    directory.mkdir()
    (directory / "index.html").write_text(
        "<html><head><title>Demo</title></head><body><h1>Hi</h1></body></html>", encoding="utf-8"
    )
    (directory / "robots.txt").write_text("User-agent: *\nDisallow:\n", encoding="utf-8")
    return tmp_path


# --- which hosts it answers for ---------------------------------------------------------


def test_it_answers_for_a_reserved_host_whether_a_fixture_exists_or_not(site: Any) -> None:
    backend = FixtureFetchBackend(root=str(site))

    assert backend.handles("demo.invalid") is True
    assert backend.handles("nothing-here.invalid") is True


def test_it_answers_for_a_checked_in_host_that_is_not_reserved(tmp_path: Any) -> None:
    """The demo builder-site records live on real suffixes, and must still stay offline."""
    (tmp_path / "builder.example.com").mkdir()
    backend = FixtureFetchBackend(root=str(tmp_path))

    assert backend.handles("builder.example.com") is True


def test_it_declines_a_real_host_it_has_no_fixture_for(site: Any) -> None:
    backend = FixtureFetchBackend(root=str(site))

    assert backend.handles("www.google.com") is False
    assert backend.handles("some-real-business.co.uk") is False


def test_a_reserved_host_with_no_fixture_fails_to_connect(site: Any) -> None:
    """Which is exactly what the network would do: `.invalid` can never resolve."""
    backend = FixtureFetchBackend(root=str(site))

    with pytest.raises(ConnectFailedError) as exc:
        backend.get(request("https://nothing-here.invalid/"))

    assert "no demo site fixture" in str(exc.value)


def test_the_www_form_of_a_host_reaches_the_same_fixture(site: Any) -> None:
    backend = FixtureFetchBackend(root=str(site))

    response = backend.get(request("https://www.demo.invalid/"))

    assert response.status_code == 200
    assert b"Demo" in response.body


def test_a_host_that_tries_to_escape_the_fixture_tree_is_not_found(site: Any) -> None:
    assert demo_site_dir("../../etc", root=str(site)) is None


def test_the_backend_chain_only_includes_fixtures_where_they_are_allowed() -> None:
    with_fixtures = default_backends(allow_fixtures=True)
    without = default_backends(allow_fixtures=False)

    assert isinstance(with_fixtures[0], FixtureFetchBackend)
    assert isinstance(with_fixtures[-1], NetworkFetchBackend)
    assert [type(b) for b in without] == [NetworkFetchBackend]


@pytest.mark.parametrize(
    ("environment", "allowed"),
    [
        ("local", True),
        ("development", True),
        ("ci", True),
        ("staging", False),
        ("production", False),
    ],
)
def test_fixtures_are_allowed_only_outside_staging_and_production(
    environment: str, allowed: bool
) -> None:
    from pydantic import SecretStr

    settings = Settings(environment=environment, jwt_secret=SecretStr("x" * 32))  # type: ignore[arg-type]

    assert settings.fixtures_allowed is allowed


# --- what it answers with ----------------------------------------------------------------


def test_the_homepage_and_robots_come_from_their_own_files(site: Any) -> None:
    backend = FixtureFetchBackend(root=str(site))

    home = backend.get(request("https://demo.invalid/"))
    robots = backend.get(request("https://demo.invalid/robots.txt"))

    assert home.header("content-type") == "text/html; charset=utf-8"
    assert b"<title>Demo</title>" in home.body
    assert robots.header("content-type") == "text/plain; charset=utf-8"
    assert b"User-agent: *" in robots.body


def test_every_other_path_reads_the_one_homepage_file(site: Any) -> None:
    """The audit is homepage-only, so a fixture never pretends to be a whole site."""
    backend = FixtureFetchBackend(root=str(site))

    response = backend.get(request("https://demo.invalid/services/water-heaters"))

    assert b"<title>Demo</title>" in response.body


def test_a_missing_robots_file_is_a_404(tmp_path: Any) -> None:
    (tmp_path / "bare.invalid").mkdir()
    (tmp_path / "bare.invalid" / "index.html").write_text("<html></html>", encoding="utf-8")

    response = FixtureFetchBackend(root=str(tmp_path)).get(
        request("https://bare.invalid/robots.txt")
    )

    assert response.status_code == 404


def write_meta(directory: Any, meta: dict[str, Any]) -> None:
    with open(os.path.join(directory, "_meta.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle)


def test_meta_can_set_a_status_and_an_inline_body(site: Any) -> None:
    write_meta(site / "demo.invalid", {"paths": {"/robots.txt": {"status": 503, "body": "down"}}})

    response = FixtureFetchBackend(root=str(site)).get(request("https://demo.invalid/robots.txt"))

    assert response.status_code == 503
    assert response.body == b"down"


def test_meta_can_redirect_one_scheme_only(site: Any) -> None:
    write_meta(
        site / "demo.invalid",
        {"paths": {"http:/": {"status": 301, "headers": {"location": "https://demo.invalid/"}}}},
    )
    backend = FixtureFetchBackend(root=str(site))

    over_http = backend.get(request("http://demo.invalid/"))
    over_https = backend.get(request("https://demo.invalid/"))

    assert over_http.status_code == 301
    assert over_http.header("location") == "https://demo.invalid/"
    assert over_https.status_code == 200


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("tls", TlsVerificationError),
        ("timeout", FetchTimeoutError),
        ("connect", ConnectFailedError),
    ],
)
def test_meta_can_simulate_a_failure(site: Any, failure: str, expected: type[Exception]) -> None:
    write_meta(site / "demo.invalid", {"failure": failure})

    with pytest.raises(expected):
        FixtureFetchBackend(root=str(site)).get(request("https://demo.invalid/"))


def test_a_fixture_body_over_the_cap_is_cut_off(tmp_path: Any) -> None:
    directory = tmp_path / "big.invalid"
    directory.mkdir()
    (directory / "index.html").write_text("x" * 5000, encoding="utf-8")

    response = FixtureFetchBackend(root=str(tmp_path)).get(
        request("https://big.invalid/", max_bytes=1000)
    )

    assert len(response.body) == 1000
    assert response.truncated is True


# --- the checked-in demo sites -----------------------------------------------------------


def test_the_checked_in_demo_sites_are_all_readable() -> None:
    """One directory per host, each answerable and each with valid metadata."""
    root = demo_sites_root()
    hosts = [name for name in sorted(os.listdir(root)) if os.path.isdir(os.path.join(root, name))]
    backend = FixtureFetchBackend()

    assert len(hosts) >= 10, "the spec asks for at least ten demo sites"
    for host in hosts:
        assert backend.handles(host), host
        assert os.path.isfile(os.path.join(root, host, "index.html")), host
        meta_path = os.path.join(root, host, "_meta.json")
        if os.path.isfile(meta_path):
            with open(meta_path, encoding="utf-8") as handle:
                assert isinstance(json.load(handle), dict), host


def test_no_demo_site_is_on_a_host_somebody_else_could_own() -> None:
    """Either reserved under `.invalid`, or a builder subdomain from the demo fixture."""
    import json as json_module
    from pathlib import Path

    demo = json_module.loads(
        (Path(demo_sites_root()).parent / "austin_plumbers.json").read_text(encoding="utf-8")
    )
    listed = {(place.get("websiteUri") or "").lower() for place in demo["places"]}
    root = demo_sites_root()
    hosts = [name for name in sorted(os.listdir(root)) if os.path.isdir(os.path.join(root, name))]

    for host in hosts:
        if host.endswith(".invalid"):
            continue
        assert any(host in url for url in listed), (
            f"{host} is neither reserved nor a host the demo fixture lists"
        )
