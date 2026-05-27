#!/usr/bin/env bash
set -euo pipefail

cat > /app/inventory_processor.py <<'PY'
from __future__ import annotations

import json
from pathlib import Path

EVENTS_PATH = Path("/app/events.json")
INVENTORY_PATH = Path("/app/final_inventory.txt")
ORDERS_PATH = Path("/app/orders.txt")
ERRORS_PATH = Path("/app/errors.txt")


def canonical_event(event: dict) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"))


def is_positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_restock(event: dict) -> str | None:
    if "sku" not in event or not isinstance(event["sku"], str) or not event["sku"]:
        return "invalid restock sku"
    if not is_positive_int(event.get("quantity")):
        return "invalid restock quantity"
    return None


def validate_items(items) -> tuple[list[dict] | None, str | None]:
    if not isinstance(items, list) or not items:
        return None, "invalid hold items"

    parsed: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            return None, "invalid hold items"
        sku = item.get("sku")
        quantity = item.get("quantity")
        if not isinstance(sku, str) or not sku:
            return None, "invalid hold items"
        if not is_positive_int(quantity) or quantity == 0:
            return None, "invalid hold items"
        parsed.append({"sku": sku, "quantity": quantity})

    return parsed, None


def validate_hold(event: dict) -> tuple[dict | None, str | None]:
    order_id = event.get("order_id")
    if not isinstance(order_id, str) or not order_id:
        return None, "invalid hold order_id"

    items, item_error = validate_items(event.get("items"))
    if item_error:
        return None, item_error

    ttl = event.get("ttl")
    if not is_positive_int(ttl) or ttl == 0:
        return None, "invalid hold ttl"

    return {"order_id": order_id, "items": items, "ttl": ttl}, None


def validate_order_ref(event: dict) -> tuple[str | None, str | None]:
    order_id = event.get("order_id")
    if not isinstance(order_id, str) or not order_id:
        return None, "invalid order reference"
    return order_id, None


class Processor:
    def __init__(self) -> None:
        self.inventory: dict[str, int] = {}
        self.orders: dict[str, dict] = {}
        self.seen_events: dict[str, str] = {}
        self.errors: list[tuple[int, int, str, str]] = []

    def add_error(self, event: dict, input_index: int, reason: str) -> None:
        self.errors.append((event.get("time", 0), input_index, event.get("event_id", ""), reason))

    def release_expired_holds(self, current_time: int) -> None:
        for order_id, order in list(self.orders.items()):
            if order.get("status") != "held":
                continue
            if order["expires_at"] <= current_time:
                for item in order["items"]:
                    sku = item["sku"]
                    quantity = item["quantity"]
                    self.inventory[sku] = self.inventory.get(sku, 0) + quantity
                order["status"] = "expired"

    def available(self, sku: str) -> int:
        return self.inventory.get(sku, 0)

    def can_reserve(self, items: list[dict]) -> bool:
        return all(self.available(item["sku"]) >= item["quantity"] for item in items)

    def reserve(self, items: list[dict]) -> None:
        for item in items:
            sku = item["sku"]
            self.inventory[sku] = self.available(sku) - item["quantity"]

    def release(self, items: list[dict]) -> None:
        for item in items:
            sku = item["sku"]
            self.inventory[sku] = self.available(sku) + item["quantity"]

    def process_event(self, event: dict, input_index: int) -> None:
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            self.add_error(event, input_index, "missing event_id")
            return

        if "time" not in event or not isinstance(event["time"], int) or isinstance(event["time"], bool):
            self.add_error(event, input_index, "invalid time")
            return

        event_type = event.get("type")
        if not isinstance(event_type, str):
            self.add_error(event, input_index, "unknown event type")
            return

        canonical = canonical_event(event)
        if event_id in self.seen_events:
            if self.seen_events[event_id] == canonical:
                return
            self.add_error(event, input_index, "duplicate event_id with different content")
            return

        self.seen_events[event_id] = canonical

        if event_type == "RESTOCK":
            error = validate_restock(event)
            if error:
                self.add_error(event, input_index, error)
                return
            sku = event["sku"]
            self.inventory[sku] = self.available(sku) + event["quantity"]
            return

        if event_type == "HOLD":
            hold_data, error = validate_hold(event)
            if error:
                self.add_error(event, input_index, error)
                return

            order_id = hold_data["order_id"]
            items = hold_data["items"]
            ttl = hold_data["ttl"]

            existing = self.orders.get(order_id)
            if existing and existing["status"] == "held":
                self.add_error(event, input_index, "order already has active hold")
                return

            if not self.can_reserve(items):
                self.orders[order_id] = {
                    "status": "rejected",
                    "items": items,
                    "expires_at": event["time"] + ttl,
                }
                self.add_error(event, input_index, "hold rejected")
                return

            self.reserve(items)
            self.orders[order_id] = {
                "status": "held",
                "items": items,
                "expires_at": event["time"] + ttl,
            }
            return

        if event_type == "PURCHASE":
            order_id, error = validate_order_ref(event)
            if error:
                self.add_error(event, input_index, error)
                return

            order = self.orders.get(order_id)
            if not order or order["status"] != "held":
                self.add_error(event, input_index, "purchase requires active hold")
                return

            order["status"] = "purchased"
            return

        if event_type == "CANCEL":
            order_id, error = validate_order_ref(event)
            if error:
                self.add_error(event, input_index, error)
                return

            order = self.orders.get(order_id)
            if not order or order["status"] != "held":
                self.add_error(event, input_index, "cancel requires active hold")
                return

            self.release(order["items"])
            order["status"] = "cancelled"
            return

        self.add_error(event, input_index, "unknown event type")

    def write_outputs(self) -> None:
        inventory_lines = [
            f"{sku}, {self.inventory[sku]}"
            for sku in sorted(self.inventory)
        ]
        INVENTORY_PATH.write_text(
            ("\n".join(inventory_lines) + ("\n" if inventory_lines else "")),
            encoding="utf-8",
        )

        order_lines = [
            f"{order_id}, {order['status']}"
            for order_id, order in sorted(self.orders.items())
        ]
        ORDERS_PATH.write_text(
            ("\n".join(order_lines) + ("\n" if order_lines else "")),
            encoding="utf-8",
        )

        self.errors.sort(key=lambda item: (item[0], item[1]))
        error_lines = [f"{event_id}, {reason}" for _, _, event_id, reason in self.errors]
        ERRORS_PATH.write_text(
            ("\n".join(error_lines) + ("\n" if error_lines else "")),
            encoding="utf-8",
        )


def main() -> None:
    raw_events = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw_events, list):
        raise SystemExit("events.json must contain a JSON list")

    indexed_events = list(enumerate(raw_events))
    indexed_events.sort(key=lambda pair: (pair[1].get("time", 0), pair[0]))

    processor = Processor()
    current_time: int | None = None

    for input_index, event in indexed_events:
        if not isinstance(event, dict):
            processor.add_error({"event_id": "", "time": 0}, input_index, "invalid event")
            continue

        event_time = event.get("time", 0) if isinstance(event.get("time"), int) else 0
        if current_time is None or event_time != current_time:
            processor.release_expired_holds(event_time)
            current_time = event_time

        processor.process_event(event, input_index)

    processor.write_outputs()


if __name__ == "__main__":
    main()
PY
