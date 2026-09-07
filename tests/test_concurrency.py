"""Two tills reaching for the same thing at the same moment.

sales.create_sale promises that "two tills racing for the last unit cannot
both succeed", and giftcards.redeem that "two tills cannot both spend the last
dollar". Both are claims about what happens when two transactions interleave,
and nothing in the suite had ever made two of them interleave -- the promises
were argued rather than observed.

They hold. These tests hold them, so that a later change which moves selling
onto the background worker, or points a second machine at one database, finds
out here rather than at a counter.

Every thread opens its own connection: db keeps them thread-local, which is
what makes this safe to do at all, and each closes its own on the way out.
"""

from __future__ import annotations

import queue
import threading
import unittest

from app import db
from app.services import giftcards
from app.services import products as products_service
from app.services import sales as sales_service
from tests.support import DatabaseTestCase

RACERS = 6


def race(action, racers: int = RACERS):
    """Run ``action`` on several threads released at the same instant.

    Returns (successes, failures) where a failure is its exception.
    """
    start = threading.Barrier(racers)
    outcomes: queue.Queue = queue.Queue()

    def run() -> None:
        try:
            start.wait(timeout=10)
            outcomes.put(("ok", action()))
        except Exception as exc:  # noqa: BLE001 - losing the race is an outcome
            outcomes.put(("failed", exc))
        finally:
            db.close_connection()

    threads = [threading.Thread(target=run, name=f"racer-{n}")
               for n in range(racers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    results = [outcomes.get() for _ in range(racers)]
    return ([value for kind, value in results if kind == "ok"],
            [value for kind, value in results if kind == "failed"])


class LastUnitTests(DatabaseTestCase):
    """"Two tills racing for the last unit cannot both succeed." """

    def test_only_one_till_can_sell_the_last_one(self):
        product_id = products_service.create_product(
            sku="LAST-1", name="Last one", price_usd="10.00", cost_usd="4.00",
            stock_qty=1,
        )

        def sell():
            cart = sales_service.Cart()
            cart.add_product(products_service.get_product(product_id), 1)
            return sales_service.create_sale(
                user_id=self.admin.user_id, cart=cart,
                payment_method="Cash", amount_paid=100,
            )

        sold, refused = race(sell)

        self.assertEqual(len(sold), 1, f"{len(sold)} tills sold the same unit")
        self.assertEqual(len(refused), RACERS - 1)
        self.assertEqual(products_service.get_product(product_id)["stock_qty"], 0)

    def test_the_losers_leave_nothing_behind(self):
        """A refused sale must not have written an invoice or moved stock."""
        product_id = products_service.create_product(
            sku="LAST-2", name="Last one", price_usd="10.00", cost_usd="4.00",
            stock_qty=1,
        )

        def sell():
            cart = sales_service.Cart()
            cart.add_product(products_service.get_product(product_id), 1)
            return sales_service.create_sale(
                user_id=self.admin.user_id, cart=cart,
                payment_method="Cash", amount_paid=100,
            )

        race(sell)

        self.assertEqual(db.scalar("SELECT COUNT(*) FROM sales", default=0), 1)
        self.assertEqual(
            db.scalar("SELECT COALESCE(SUM(change_qty), 0) FROM inventory_log "
                      "WHERE product_id = ? AND reason = 'Sale'",
                      (product_id,), default=0),
            -1,
            "more than one sale took stock from a product that had one unit",
        )


class LastDollarTests(DatabaseTestCase):
    """"Two tills cannot both spend the last dollar." """

    def test_a_card_cannot_be_spent_twice_at_once(self):
        card = giftcards.issue(20, user_id=self.admin.user_id)
        product_id = products_service.create_product(
            sku="GC-1", name="Thing", price_usd="20.00", cost_usd="4.00",
            stock_qty=RACERS * 2,
        )

        def spend():
            cart = sales_service.Cart()
            cart.add_product(products_service.get_product(product_id), 1)
            return sales_service.create_sale(
                user_id=self.admin.user_id, cart=cart, payment_method="Cash",
                gift_card_code=card["code"], gift_card_amount=20, amount_paid=100,
            )

        spent, refused = race(spend)

        self.assertEqual(len(spent), 1, f"the card paid for {len(spent)} sales")
        self.assertEqual(len(refused), RACERS - 1)
        self.assertEqual(giftcards.get_by_code(card["code"])["balance_usd"], 0)

    def test_the_card_ledger_still_matches_its_balance(self):
        card = giftcards.issue(20, user_id=self.admin.user_id)
        product_id = products_service.create_product(
            sku="GC-2", name="Thing", price_usd="20.00", cost_usd="4.00",
            stock_qty=RACERS * 2,
        )

        def spend():
            cart = sales_service.Cart()
            cart.add_product(products_service.get_product(product_id), 1)
            return sales_service.create_sale(
                user_id=self.admin.user_id, cart=cart, payment_method="Cash",
                gift_card_code=card["code"], gift_card_amount=20, amount_paid=100,
            )

        race(spend)

        row = giftcards.get_by_code(card["code"])
        events = db.scalar(
            "SELECT COALESCE(SUM(amount_usd), 0) FROM gift_card_events "
            "WHERE card_id = ? AND kind != 'Sale'", (row["card_id"],), default=0)
        self.assertAlmostEqual(row["balance_usd"], events, places=2)


class ReferenceCollisionTests(DatabaseTestCase):
    """What happens when two sales are written in the same instant.

    An invoice number is the highest one used today plus one, read inside the
    transaction that writes it. Two transactions can read the same highest
    number, and then the UNIQUE column decides between them: one commits, the
    other is refused and rolls back whole.

    That is the outcome worth having, and it is what this pins. The refusal
    surfaces as a raw sqlite3.IntegrityError rather than a sale error, which
    would be worth smoothing if it were reachable -- every sale, refund and
    layaway collection is called from a view method on Tk's single UI thread,
    and the background worker only ever runs backups, the printer list, the
    integrity check and the update check. It takes a second process against
    one database to see it at all.
    """

    def test_two_sales_at_once_never_share_an_invoice_number(self):
        product_id = products_service.create_product(
            sku="RACE-1", name="Plentiful", price_usd="1.00", cost_usd="0.50",
            stock_qty=1000,
        )

        def sell():
            cart = sales_service.Cart()
            cart.add_product(products_service.get_product(product_id), 1)
            return sales_service.create_sale(
                user_id=self.admin.user_id, cart=cart,
                payment_method="Cash", amount_paid=10,
            )

        sold, _refused = race(sell)

        self.assertGreaterEqual(len(sold), 1, "every racer failed")
        written = db.scalar("SELECT COUNT(*) FROM sales", default=0)
        distinct = db.scalar("SELECT COUNT(DISTINCT invoice_no) FROM sales", default=0)
        self.assertEqual(written, distinct, "two sales share an invoice number")
        self.assertEqual(written, len(sold), "a refused sale was written anyway")

    def test_a_refused_sale_takes_no_stock(self):
        product_id = products_service.create_product(
            sku="RACE-2", name="Plentiful", price_usd="1.00", cost_usd="0.50",
            stock_qty=1000,
        )

        def sell():
            cart = sales_service.Cart()
            cart.add_product(products_service.get_product(product_id), 1)
            return sales_service.create_sale(
                user_id=self.admin.user_id, cart=cart,
                payment_method="Cash", amount_paid=10,
            )

        sold, _refused = race(sell)

        self.assertEqual(
            products_service.get_product(product_id)["stock_qty"],
            1000 - len(sold),
            "stock moved for a sale that was rolled back",
        )


if __name__ == "__main__":
    unittest.main()
