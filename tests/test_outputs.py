import json
import subprocess
from pathlib import Path

APP_DIR = Path("/app")
EVENTS_PATH = APP_DIR / "events.json"
INVENTORY_PATH = APP_DIR / "final_inventory.txt"
ORDERS_PATH = APP_DIR / "orders.txt"
ERRORS_PATH = APP_DIR / "errors.txt"
SCRIPT_PATH = APP_DIR / "inventory_processor.py"


def run_processor(events):
    for path in [EVENTS_PATH, INVENTORY_PATH, ORDERS_PATH, ERRORS_PATH]:
        if path.exists():
            path.unlink()

    EVENTS_PATH.write_text(json.dumps(events, indent=2), encoding="utf-8")

    result = subprocess.run(
        ["python", str(SCRIPT_PATH)],
        cwd=str(APP_DIR),
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, (
        f"processor failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    assert INVENTORY_PATH.exists(), "final_inventory.txt was not created"
    assert ORDERS_PATH.exists(), "orders.txt was not created"
    assert ERRORS_PATH.exists(), "errors.txt was not created"

    return {
        "inventory": read_lines(INVENTORY_PATH),
        "orders": read_lines(ORDERS_PATH),
        "errors": read_lines(ERRORS_PATH),
    }


def read_lines(path):
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def parse_mapping(lines):
    result = {}
    for line in lines:
        left, right = [part.strip() for part in line.split(",", 1)]
        result[left] = right
    return result


def error_ids(lines):
    ids = []
    for line in lines:
        ids.append(line.split(",", 1)[0].strip())
    return ids


def test_basic_hold_purchase_flow():
    events = [
        {
            "event_id": "e1",
            "time": 1,
            "type": "RESTOCK",
            "sku": "shirt",
            "quantity": 10,
        },
        {
            "event_id": "e2",
            "time": 2,
            "type": "HOLD",
            "order_id": "o1",
            "items": [{"sku": "shirt", "quantity": 2}],
            "ttl": 5,
        },
        {
            "event_id": "e3",
            "time": 4,
            "type": "PURCHASE",
            "order_id": "o1",
        },
    ]

    out = run_processor(events)

    assert out["inventory"] == ["shirt, 8"]
    assert out["orders"] == ["o1, purchased"]
    assert out["errors"] == []


def test_events_are_processed_chronologically_not_input_order():
    events = [
        {
            "event_id": "e2",
            "time": 5,
            "type": "HOLD",
            "order_id": "o1",
            "items": [{"sku": "book", "quantity": 3}],
            "ttl": 10,
        },
        {
            "event_id": "e1",
            "time": 1,
            "type": "RESTOCK",
            "sku": "book",
            "quantity": 3,
        },
    ]

    out = run_processor(events)

    assert out["inventory"] == ["book, 0"]
    assert out["orders"] == ["o1, held"]
    assert out["errors"] == []


def test_same_timestamp_keeps_original_input_order():
    events = [
        {
            "event_id": "e1",
            "time": 1,
            "type": "HOLD",
            "order_id": "o1",
            "items": [{"sku": "hat", "quantity": 1}],
            "ttl": 5,
        },
        {
            "event_id": "e2",
            "time": 1,
            "type": "RESTOCK",
            "sku": "hat",
            "quantity": 1,
        },
    ]

    out = run_processor(events)

    assert out["inventory"] == ["hat, 1"]
    assert out["orders"] == ["o1, rejected"]
    assert error_ids(out["errors"]) == ["e1"]


def test_expired_hold_releases_inventory_before_events_at_same_timestamp():
    events = [
        {
            "event_id": "e1",
            "time": 1,
            "type": "RESTOCK",
            "sku": "console",
            "quantity": 5,
        },
        {
            "event_id": "e2",
            "time": 2,
            "type": "HOLD",
            "order_id": "o1",
            "items": [{"sku": "console", "quantity": 5}],
            "ttl": 3,
        },
        {
            "event_id": "e3",
            "time": 5,
            "type": "HOLD",
            "order_id": "o2",
            "items": [{"sku": "console", "quantity": 5}],
            "ttl": 10,
        },
    ]

    out = run_processor(events)

    assert out["inventory"] == ["console, 0"]
    assert out["orders"] == ["o1, expired", "o2, held"]
    assert out["errors"] == []


def test_purchase_at_exact_expiration_time_is_invalid():
    events = [
        {
            "event_id": "e1",
            "time": 1,
            "type": "RESTOCK",
            "sku": "phone",
            "quantity": 2,
        },
        {
            "event_id": "e2",
            "time": 2,
            "type": "HOLD",
            "order_id": "o1",
            "items": [{"sku": "phone", "quantity": 2}],
            "ttl": 3,
        },
        {
            "event_id": "e3",
            "time": 5,
            "type": "PURCHASE",
            "order_id": "o1",
        },
    ]

    out = run_processor(events)

    assert out["inventory"] == ["phone, 2"]
    assert out["orders"] == ["o1, expired"]
    assert error_ids(out["errors"]) == ["e3"]


def test_duplicate_event_with_same_content_is_idempotent():
    event = {
        "event_id": "e1",
        "time": 1,
        "type": "RESTOCK",
        "sku": "socks",
        "quantity": 5,
    }

    out = run_processor([event, dict(event)])

    assert out["inventory"] == ["socks, 5"]
    assert out["orders"] == []
    assert out["errors"] == []


def test_duplicate_event_id_with_different_content_is_error_and_not_applied():
    events = [
        {
            "event_id": "e1",
            "time": 1,
            "type": "RESTOCK",
            "sku": "socks",
            "quantity": 5,
        },
        {
            "event_id": "e1",
            "time": 2,
            "type": "RESTOCK",
            "sku": "socks",
            "quantity": 8,
        },
    ]

    out = run_processor(events)

    assert out["inventory"] == ["socks, 5"]
    assert out["orders"] == []
    assert error_ids(out["errors"]) == ["e1"]


def test_multi_sku_hold_is_all_or_none():
    events = [
        {
            "event_id": "e1",
            "time": 1,
            "type": "RESTOCK",
            "sku": "shirt",
            "quantity": 1,
        },
        {
            "event_id": "e2",
            "time": 1,
            "type": "RESTOCK",
            "sku": "pants",
            "quantity": 0,
        },
        {
            "event_id": "e3",
            "time": 2,
            "type": "HOLD",
            "order_id": "o1",
            "items": [
                {"sku": "shirt", "quantity": 1},
                {"sku": "pants", "quantity": 1},
            ],
            "ttl": 5,
        },
    ]

    out = run_processor(events)

    assert out["inventory"] == ["pants, 0", "shirt, 1"]
    assert out["orders"] == ["o1, rejected"]
    assert error_ids(out["errors"]) == ["e3"]


def test_cancel_after_purchase_does_not_release_inventory():
    events = [
        {
            "event_id": "e1",
            "time": 1,
            "type": "RESTOCK",
            "sku": "camera",
            "quantity": 2,
        },
        {
            "event_id": "e2",
            "time": 2,
            "type": "HOLD",
            "order_id": "o1",
            "items": [{"sku": "camera", "quantity": 2}],
            "ttl": 10,
        },
        {
            "event_id": "e3",
            "time": 3,
            "type": "PURCHASE",
            "order_id": "o1",
        },
        {
            "event_id": "e4",
            "time": 4,
            "type": "CANCEL",
            "order_id": "o1",
        },
    ]

    out = run_processor(events)

    assert out["inventory"] == ["camera, 0"]
    assert out["orders"] == ["o1, purchased"]
    assert error_ids(out["errors"]) == ["e4"]


def test_cancel_active_hold_releases_inventory():
    events = [
        {
            "event_id": "e1",
            "time": 1,
            "type": "RESTOCK",
            "sku": "keyboard",
            "quantity": 4,
        },
        {
            "event_id": "e2",
            "time": 2,
            "type": "HOLD",
            "order_id": "o1",
            "items": [{"sku": "keyboard", "quantity": 3}],
            "ttl": 10,
        },
        {
            "event_id": "e3",
            "time": 3,
            "type": "CANCEL",
            "order_id": "o1",
        },
    ]

    out = run_processor(events)

    assert out["inventory"] == ["keyboard, 4"]
    assert out["orders"] == ["o1, cancelled"]
    assert out["errors"] == []


def test_malformed_and_unknown_order_events_are_reported_but_do_not_stop_processing():
    events = [
        {
            "event_id": "e1",
            "time": 1,
            "type": "RESTOCK",
            "sku": "bag",
            "quantity": 2,
        },
        {
            "event_id": "bad_negative",
            "time": 2,
            "type": "RESTOCK",
            "sku": "bag",
            "quantity": -5,
        },
        {
            "event_id": "bad_unknown_order",
            "time": 3,
            "type": "PURCHASE",
            "order_id": "missing",
        },
        {
            "event_id": "e4",
            "time": 4,
            "type": "HOLD",
            "order_id": "o1",
            "items": [{"sku": "bag", "quantity": 1}],
            "ttl": 5,
        },
    ]

    out = run_processor(events)

    assert out["inventory"] == ["bag, 1"]
    assert out["orders"] == ["o1, held"]
    assert error_ids(out["errors"]) == ["bad_negative", "bad_unknown_order"]


def test_outputs_are_sorted_deterministically():
    events = [
        {
            "event_id": "e1",
            "time": 1,
            "type": "RESTOCK",
            "sku": "zebra",
            "quantity": 1,
        },
        {
            "event_id": "e2",
            "time": 1,
            "type": "RESTOCK",
            "sku": "apple",
            "quantity": 2,
        },
        {
            "event_id": "e3",
            "time": 2,
            "type": "HOLD",
            "order_id": "order_b",
            "items": [{"sku": "zebra", "quantity": 1}],
            "ttl": 10,
        },
        {
            "event_id": "e4",
            "time": 2,
            "type": "HOLD",
            "order_id": "order_a",
            "items": [{"sku": "apple", "quantity": 1}],
            "ttl": 10,
        },
    ]

    out = run_processor(events)

    assert out["inventory"] == ["apple, 1", "zebra, 0"]
    assert out["orders"] == ["order_a, held", "order_b, held"]
    assert out["errors"] == []
