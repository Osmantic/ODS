from __future__ import annotations

from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.server import x402ResourceServer

from .config import GatewayConfig


def _price(amount: str) -> str:
    return f"${amount}"


def build_x402_routes(config: GatewayConfig) -> dict[str, RouteConfig]:
    routes: dict[str, RouteConfig] = {}
    for rule in config.rules:
        for method in rule.methods:
            route_key = f"{method.upper()} {rule.path}"
            routes[route_key] = RouteConfig(
                accepts=[
                    PaymentOption(
                        scheme="exact",
                        pay_to=config.seller.recipient,
                        price=_price(rule.price.amount),
                        network=config.seller.network,
                    ),
                ],
                mime_type=rule.metadata.mimeType,
                description=rule.metadata.description or rule.name,
            )
    return routes


def build_resource_server(config: GatewayConfig):
    facilitator = HTTPFacilitatorClient(
        FacilitatorConfig(url=str(config.seller.facilitatorUrl))
    )
    server = x402ResourceServer(facilitator)
    server.register(config.seller.network, ExactEvmServerScheme())
    return server
