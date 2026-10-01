"""SPF and DMARC parsing (spec v0.13.0, task 8), and what is never kept (decision C8)."""

import pytest

from app.modules.domain_intel import records


def test_multiple_spf_records_are_all_kept() -> None:
    split = records.split_apex_txt(["v=spf1 -all", "V=SPF1 include:x.com ~all", "other"])
    assert split.spf == ("v=spf1 -all", "V=SPF1 include:x.com ~all")
    assert split.other_count == 1


def test_a_record_that_only_looks_like_spf_is_not_spf() -> None:
    assert records.split_apex_txt(["v=spf10 -all", "spf1 -all"]).spf == ()


@pytest.mark.parametrize(
    ("record", "mechanism", "allows_all"),
    [
        ("v=spf1 include:_spf.google.com all", "all", True),
        ("v=spf1 +all", "+all", True),
        ("v=spf1 mx -all", "-all", False),
        ("v=spf1 mx ~all", "~all", False),
        ("v=spf1 mx ?all", "?all", False),
        ("v=spf1 mx", None, False),
        ("v=spf1 include:allmail.example.com -all", "-all", False),
    ],
)
def test_the_all_mechanism(record: str, mechanism: str | None, allows_all: bool) -> None:
    assert records.spf_all(record) == mechanism
    assert records.spf_allows_all(record) is allows_all


@pytest.mark.parametrize(
    ("record", "policy"),
    [
        ("v=DMARC1; p=none", "none"),
        ("v=DMARC1; p=quarantine; pct=50", "quarantine"),
        ("v=DMARC1;p=reject", "reject"),
        ("v=DMARC1; P=Reject", "reject"),
        ("v=DMARC1; rua=mailto:a@b.com", None),
    ],
)
def test_dmarc_policy(record: str, policy: str | None) -> None:
    (parsed,) = records.split_dmarc_txt([record]).records
    assert records.dmarc_policy(parsed) == policy


def test_dmarc_keeps_v_p_and_sp_only() -> None:
    parsed = records.dmarc_tags(
        "v=DMARC1; p=quarantine; sp=none; rua=mailto:owner@acme.com; ruf=mailto:x@acme.com; fo=1"
    )
    assert parsed == {"v": "DMARC1", "p": "quarantine", "sp": "none"}
    assert records.dmarc_text(parsed) == "v=DMARC1; p=quarantine; sp=none"


def test_a_txt_at_dmarc_that_is_not_dmarc_is_only_counted() -> None:
    split = records.split_dmarc_txt(["hello owner@acme.com", "v=DMARC1; p=reject"])
    assert split.records == ({"v": "DMARC1", "p": "reject"},)
    assert split.other_count == 1
