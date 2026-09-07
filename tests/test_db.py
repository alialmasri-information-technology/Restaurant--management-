"""The database layer itself.

Seven hundred lines that every other module sits on, and the only one with no
test file of its own -- it was covered incidentally by all of them and examined
by none. What is pinned here is the part with a sharp edge: transaction()
nests, and the nesting changes what a caller is allowed to do with an error.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from app import db
from tests.support import DatabaseTestCase

APP = pathlib.Path(__file__).resolve().parent.parent / "app"


class TransactionNestingTests(DatabaseTestCase):
    opens_shift = False

    def setUp(self):
        super().setUp()
        db.execute("CREATE TABLE probe (n INTEGER)")

    def rows(self):
        return [row["n"] for row in db.query("SELECT n FROM probe ORDER BY n")]

    def test_an_inner_write_is_undone_when_the_outer_block_fails(self):
        """The reason transaction() is reentrant at all.

        db.execute opens a transaction of its own. Were that not folded into
        the enclosing one, it would commit on the way out and the rollback
        below would have nothing left to undo.
        """
        with self.assertRaises(RuntimeError), db.transaction():
            db.execute("INSERT INTO probe VALUES (1)")
            raise RuntimeError("the unit of work fails after the write")
        self.assertEqual(self.rows(), [])

    def test_the_whole_unit_of_work_commits_together(self):
        with db.transaction():
            db.execute("INSERT INTO probe VALUES (1)")
            with db.transaction():
                db.execute("INSERT INTO probe VALUES (2)")
        self.assertEqual(self.rows(), [1, 2])

    def test_a_swallowed_inner_failure_leaves_its_writes_behind(self):
        """The sharp edge, pinned so it is not discovered by an audit instead.

        An inner block cannot undo itself -- only the outermost one rolls
        back. A caller that catches an inner failure and carries on therefore
        keeps the half-written rows, and the outer block commits them.

        This is the documented contract rather than a bug to fix: closing it
        with nested SAVEPOINTs would end the outer transaction early, because
        sqlite3 does not open one until the first write. The guard against it
        is the source scan below.
        """
        with db.transaction():
            db.execute("INSERT INTO probe VALUES (1)")
            with self.assertRaises(RuntimeError), db.transaction():
                db.execute("INSERT INTO probe VALUES (2)")
                raise RuntimeError("the inner unit of work fails")
            db.execute("INSERT INTO probe VALUES (3)")

        self.assertEqual(
            self.rows(), [1, 2, 3],
            "2 is the write of a block that raised; it survives because only "
            "the outermost block rolls back",
        )

    def test_the_nesting_depth_is_restored_after_a_failure(self):
        """Otherwise one failed unit of work would leave every later one
        looking nested, and nothing would ever commit again."""
        with self.assertRaises(RuntimeError), db.transaction():
            raise RuntimeError("boom")
        self.assertEqual(getattr(db._local, "depth", 0), 0)

        with db.transaction():
            db.execute("INSERT INTO probe VALUES (1)")
        self.assertEqual(self.rows(), [1])


class SwallowedFailureScanTests(unittest.TestCase):
    """Nobody may take the sharp edge above and stand on it.

    Because an inner block cannot roll itself back, catching its error and
    continuing commits work that failed. This reads the package for anyone
    doing that, so the rule outlives whoever remembers it.
    """

    def swallowing_handlers(self, path: pathlib.Path):
        found = []

        class Visitor(ast.NodeVisitor):
            def __init__(self):
                self.depth = 0

            def visit_With(self, node):
                opens = any(
                    isinstance(item.context_expr, ast.Call)
                    and ast.unparse(item.context_expr).endswith("transaction()")
                    for item in node.items
                )
                self.depth += opens
                self.generic_visit(node)
                self.depth -= opens

            def visit_Try(self, node):
                if self.depth:
                    for handler in node.handlers:
                        reraises = any(
                            isinstance(statement, (ast.Raise, ast.Return))
                            for statement in handler.body
                        )
                        if not reraises:
                            caught = (
                                ast.unparse(handler.type)
                                if handler.type
                                else "bare except"
                            )
                            found.append((handler.lineno, caught))
                self.generic_visit(node)

        Visitor().visit(ast.parse(path.read_text(encoding="utf-8")))
        return found

    def test_no_module_swallows_a_failure_inside_an_open_transaction(self):
        offenders = []
        for path in sorted(APP.rglob("*.py")):
            for line_no, caught in self.swallowing_handlers(path):
                offenders.append(f"{path.relative_to(APP.parent)}:{line_no} ({caught})")
        self.assertEqual(
            offenders, [],
            "these catch a failure while a transaction is open and carry on, "
            "which commits the writes the failed block had already made; "
            "let the error reach the outermost block instead",
        )

    def test_the_scan_can_actually_see_one(self):
        """A guard that cannot fail is not a guard."""
        source = (
            "from app import db\n"
            "def go():\n"
            "    with db.transaction():\n"
            "        try:\n"
            "            helper()\n"
            "        except ValueError:\n"
            "            pass\n"
        )
        path = pathlib.Path(self.enterContext(_temp_file(source)))
        self.assertEqual(self.swallowing_handlers(path), [(6, "ValueError")])


def _temp_file(source: str):
    import contextlib
    import tempfile

    @contextlib.contextmanager
    def opened():
        directory = tempfile.TemporaryDirectory()
        try:
            path = pathlib.Path(directory.name) / "sample.py"
            path.write_text(source, encoding="utf-8")
            yield path
        finally:
            directory.cleanup()

    return opened()


if __name__ == "__main__":
    unittest.main()
