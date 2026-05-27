# Inventory Reservation Processor

Build a small processor for an online store's inventory events.

Implement it in:

`/app/inventory_processor.py`

The program should read:

`/app/events.json`

and write:

- `/app/final_inventory.txt`
- `/app/orders.txt`
- `/app/errors.txt`

Use only the Python standard library.

## Task

The input is a JSON list of events. Events may describe inventory being restocked, inventory being reserved for an order, an order being purchased, or an order being cancelled.

Events may not be sorted. Process them in the order they happened. If multiple events happened at the same time, keep their original input order.

The processor should keep track of available inventory and order states as the events are applied.

## Event types

Supported event types:

- `RESTOCK`
- `HOLD`
- `PURCHASE`
- `CANCEL`

Holds are temporary reservations. If a held order is not purchased in time, the hold expires and its inventory becomes available again.

Orders may contain multiple items.

Duplicate, malformed, or otherwise invalid records may appear in the input. The processor should continue running and report invalid records.

## Output

### `final_inventory.txt`

One line per SKU:

```text
sku, available_quantity
```

Sort by SKU.

### `orders.txt`

One line per order:

```text
order_id, status
```

Sort by order ID.

Possible statuses:

- `held`
- `purchased`
- `cancelled`
- `expired`
- `rejected`

### `errors.txt`

One line per invalid record:

```text
event_id, reason
```

If there are no errors, create an empty file.

## Notes

Inventory should never become negative.

The output should be deterministic.
