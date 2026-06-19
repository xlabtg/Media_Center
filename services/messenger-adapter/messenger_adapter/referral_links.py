from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from cglr.link_rotator import (
    LinkRotatorError,
    LinkRouteResult,
    ReferralLink,
    generate_referral_links,
)

from libs.shared.models import JSONValue
from messenger_adapter.content_transformer import clone_json_object

if TYPE_CHECKING:
    from messenger_adapter.base_adapter import PublicationRequest

REFERRAL_ROUTE_METADATA_KEY = "referral_route"
REFERRAL_LINKS_METADATA_KEY = "referral_links"

ReferralLinkGenerator = Callable[[Mapping[str, object]], LinkRouteResult]


class ReferralLinkInjectionError(ValueError):
    """Raised when referral links cannot be injected into publication content."""


@dataclass(frozen=True, slots=True)
class ReferralLinkInjector:
    link_generator: ReferralLinkGenerator = generate_referral_links
    metadata_key: str = REFERRAL_ROUTE_METADATA_KEY

    def inject(self, request: PublicationRequest) -> PublicationRequest:
        metadata = clone_json_object(request.metadata)
        route_payload = self._route_payload(
            metadata=metadata,
            tenant_id=request.tenant_id,
            publication_id=request.publication_id,
        )
        if route_payload is None:
            return request

        try:
            route_result = self.link_generator(route_payload)
        except (LinkRotatorError, ValueError) as error:
            raise ReferralLinkInjectionError(
                "Не удалось сформировать реферальные ссылки CGLR"
            ) from error

        metadata.pop(self.metadata_key, None)
        metadata[REFERRAL_LINKS_METADATA_KEY] = _referral_metadata(route_result.links)
        data = request.model_dump(mode="python")
        data["content"] = content_with_referral_links(
            request.content,
            route_result.links,
        )
        data["metadata"] = metadata
        return type(request)(**data)

    def _route_payload(
        self,
        *,
        metadata: Mapping[str, JSONValue],
        tenant_id: str,
        publication_id: str,
    ) -> dict[str, object] | None:
        raw_payload = metadata.get(self.metadata_key)
        if raw_payload is None:
            return None
        if not isinstance(raw_payload, dict):
            raise ReferralLinkInjectionError("referral_route должен быть объектом")

        payload = cast(dict[str, object], dict(raw_payload))
        payload_tenant_id = payload.get("tenant_id")
        if payload_tenant_id is not None and payload_tenant_id != tenant_id:
            raise ReferralLinkInjectionError(
                "referral_route принадлежит другому tenant"
            )
        payload["tenant_id"] = tenant_id

        if "content_id" not in payload:
            payload["content_id"] = _metadata_content_id(metadata) or publication_id

        return payload


def content_with_referral_links(
    content: str,
    links: tuple[ReferralLink, ...],
) -> str:
    if not links:
        return content

    link_lines = [f"{link.level.value}: {link.url}" for link in links]
    return content.rstrip() + "\n\nРеферальные ссылки:\n" + "\n".join(link_lines)


def _referral_metadata(
    links: tuple[ReferralLink, ...],
) -> list[JSONValue]:
    items: list[JSONValue] = []
    for link in links:
        items.append(
            {
                "level": link.level.value,
                "owner_id": link.owner_id,
                "reward_share": link.reward_share,
            }
        )
    return items


def _metadata_content_id(metadata: Mapping[str, JSONValue]) -> str | None:
    value = metadata.get("content_id")
    if isinstance(value, str) and value.strip() != "":
        return value
    return None


__all__ = [
    "REFERRAL_LINKS_METADATA_KEY",
    "REFERRAL_ROUTE_METADATA_KEY",
    "ReferralLinkGenerator",
    "ReferralLinkInjectionError",
    "ReferralLinkInjector",
    "content_with_referral_links",
]
