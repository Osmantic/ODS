from __future__ import annotations

from .config import GatewayConfig, RouteRule


class RoutePolicy:
    def __init__(self, config: GatewayConfig) -> None:
        self._routes: dict[tuple[str, str], RouteRule] = {}
        for rule in config.rules:
            for method in rule.methods:
                self._routes[(method.upper(), rule.path)] = rule

    def match(self, method: str, path: str) -> RouteRule | None:
        return self._routes.get((method.upper(), path))

    def rules(self) -> list[RouteRule]:
        return list({id(rule): rule for rule in self._routes.values()}.values())
