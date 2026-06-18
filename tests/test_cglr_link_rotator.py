from __future__ import annotations

import pytest
from cglr.link_rotator import (
    DEFAULT_L3_MIN_CONTRIBUTION_WEIGHT,
    InMemoryReferralClickTracker,
    LinkRotationError,
    ReferralLevel,
    generate_referral_links,
)


def test_link_rotator_generates_level_links_with_referral_policy() -> None:
    result = generate_referral_links(
        tenant_id="tenant-a",
        content_id="content-001",
        rotation_seed="campaign-001",
        admin_link={
            "owner_id": "admin-main",
            "url": "https://nmc.example/join?utm=telegram",
        },
        author_link={
            "owner_id": "author-7",
            "url": "https://authors.example/author-7",
        },
        l3_candidates=[
            {
                "owner_id": "partner-low",
                "url": "https://partners.example/low",
                "contribution_weight": 9,
            },
            {
                "owner_id": "partner-a",
                "url": "https://partners.example/a",
                "contribution_weight": 10,
            },
            {
                "owner_id": "partner-b",
                "url": "https://partners.example/b",
                "contribution_weight": 30,
            },
        ],
    )

    assert [link.level for link in result.links] == [
        ReferralLevel.L1,
        ReferralLevel.L2,
        ReferralLevel.L3,
    ]
    assert [link.owner_id for link in result.links] == [
        "admin-main",
        "author-7",
        "partner-b",
    ]
    assert [link.reward_share for link in result.links] == [0.20, 0.10, 0.05]
    assert result.reward_distribution == (
        {
            "level": "L1",
            "owner_id": "admin-main",
            "reward_share": 0.20,
        },
        {
            "level": "L2",
            "owner_id": "author-7",
            "reward_share": 0.10,
        },
        {
            "level": "L3",
            "owner_id": "partner-b",
            "reward_share": 0.05,
        },
    )
    assert "utm=telegram" in result.links[0].url
    assert "nmc_level=L1" in result.links[0].url
    assert "nmc_owner_id=admin-main" in result.links[0].url
    assert result.links[2].contribution_weight == 30
    assert result.links[2].rotation_seed == "campaign-001"


def test_l3_rotation_is_deterministic_and_filters_weight_threshold() -> None:
    payload = {
        "tenant_id": "tenant-a",
        "content_id": "content-001",
        "rotation_seed": "campaign-001",
        "admin_link": {
            "owner_id": "admin-main",
            "url": "https://nmc.example/join",
        },
        "author_link": {
            "owner_id": "author-7",
            "url": "https://authors.example/author-7",
        },
        "l3_candidates": [
            {
                "owner_id": "partner-low",
                "url": "https://partners.example/low",
                "contribution_weight": DEFAULT_L3_MIN_CONTRIBUTION_WEIGHT - 0.1,
            },
            {
                "owner_id": "partner-a",
                "url": "https://partners.example/a",
                "contribution_weight": 10,
            },
            {
                "owner_id": "partner-b",
                "url": "https://partners.example/b",
                "contribution_weight": 30,
            },
        ],
    }

    first = generate_referral_links(payload)
    second = generate_referral_links(payload)

    assert first.links[2] == second.links[2]
    assert first.links[2].owner_id == "partner-b"

    with pytest.raises(LinkRotationError, match="L3"):
        generate_referral_links(
            tenant_id="tenant-a",
            content_id="content-002",
            admin_link={"owner_id": "admin-main", "url": "https://nmc.example/join"},
            author_link={
                "owner_id": "author-7",
                "url": "https://authors.example/author-7",
            },
            l3_candidates=[
                {
                    "owner_id": "partner-low",
                    "url": "https://partners.example/low",
                    "contribution_weight": 9.99,
                }
            ],
        )


def test_click_tracker_counts_referral_transitions_by_route() -> None:
    result = generate_referral_links(
        tenant_id="tenant-a",
        content_id="content-001",
        rotation_seed="campaign-001",
        admin_link={"owner_id": "admin-main", "url": "https://nmc.example/join"},
        author_link={
            "owner_id": "author-7",
            "url": "https://authors.example/author-7",
        },
        l3_candidates=[
            {
                "owner_id": "partner-b",
                "url": "https://partners.example/b",
                "contribution_weight": 30,
            }
        ],
    )
    tracker = InMemoryReferralClickTracker()

    first_l1_click = tracker.record_click(result.links[0])
    second_l1_click = tracker.record_click(result.links[0])
    l3_click = tracker.record_click(result.links[2])

    assert first_l1_click.click_count == 1
    assert second_l1_click.click_count == 2
    assert l3_click.click_count == 1
    assert tracker.total_clicks_by_level("tenant-a") == {
        ReferralLevel.L1: 2,
        ReferralLevel.L2: 0,
        ReferralLevel.L3: 1,
    }
    assert tracker.total_clicks_by_level("tenant-b") == {
        ReferralLevel.L1: 0,
        ReferralLevel.L2: 0,
        ReferralLevel.L3: 0,
    }
