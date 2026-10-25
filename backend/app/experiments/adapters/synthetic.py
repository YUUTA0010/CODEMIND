"""SYNTHETIC benchmark suite. *** Synthetic fixtures: used only to test the pipeline and to demonstrate the evaluation
framework end to end. Results on this suite say nothing about real-world bug-localization performance. ***

A small `inventory` package (pricing, cart, validators, shipping, text utils, reports) is generated, then ONE bug is
injected per task by a textual mutation. Each task records ground truth (file + symbol) that only evaluation code may
read, plus an oracle fix diff used exclusively by pipeline tests (a scripted model double) - never by retrieval.
"""
from __future__ import annotations

import difflib
import json
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

SYNTHETIC_NOTICE = "SYNTHETIC FIXTURE - generated for pipeline testing; not a real-world benchmark."


def _d(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


BASE_FILES: dict[str, str] = {
    "pytest.ini": "[pytest]\npythonpath = .\ntestpaths = tests\n",
    "README.md": f"# inventory (synthetic)\n\n{SYNTHETIC_NOTICE}\n",
    "inventory/__init__.py": f'"""{SYNTHETIC_NOTICE}"""\n',
    "inventory/models.py": _d('''
        """Domain objects."""


        class Item:
            """A product that can be placed in a cart."""

            def __init__(self, sku, name, unit_price, weight_kg=1.0):
                self.sku = sku
                self.name = name
                self.unit_price = unit_price
                self.weight_kg = weight_kg

            def __repr__(self):
                return f"Item({self.sku!r})"
    '''),
    "inventory/validators.py": _d('''
        """Input validation helpers."""
        import re

        SKU_PATTERN = re.compile(r"^[A-Z]{3}-\\d{4}$")


        class ValidationError(ValueError):
            """Raised when user supplied data is invalid."""


        def validate_sku(sku):
            """Return the SKU if it is well formed, otherwise raise ValidationError."""
            if not isinstance(sku, str) or not SKU_PATTERN.match(sku):
                raise ValidationError(f"invalid sku: {sku!r}")
            return sku


        def validate_quantity(quantity):
            """Quantities must be positive integers."""
            if not isinstance(quantity, int) or quantity <= 0:
                raise ValidationError("quantity must be a positive integer")
            return quantity
    '''),
    "inventory/pricing.py": _d('''
        """Pricing rules: bulk discounts and tax."""
        from .validators import validate_quantity

        BULK_THRESHOLD = 10
        BULK_DISCOUNT_RATE = 0.10
        TAX_RATE = 0.08


        def apply_discount(unit_price, quantity):
            """Price for `quantity` units, with a bulk discount at BULK_THRESHOLD units or more."""
            validate_quantity(quantity)
            gross = unit_price * quantity
            if quantity >= BULK_THRESHOLD:
                return round(gross * (1 - BULK_DISCOUNT_RATE), 2)
            return round(gross, 2)


        def compute_tax(amount):
            """Sales tax for an amount."""
            return round(amount * TAX_RATE, 2)
    '''),
    "inventory/legacy_pricing.py": _d('''
        """Deprecated pricing helpers kept for backwards compatibility (a deliberate lexical distractor)."""


        def apply_discount_legacy(unit_price, quantity):
            """Old flat discount without any bulk threshold."""
            return round(unit_price * quantity * 0.95, 2)
    '''),
    "inventory/cart.py": _d('''
        """Shopping cart."""
        from .pricing import apply_discount, compute_tax
        from .validators import validate_quantity, validate_sku


        class Cart:
            """Holds item lines and computes totals."""

            def __init__(self):
                self._lines = {}

            def add_item(self, item, quantity=1):
                validate_sku(item.sku)
                validate_quantity(quantity)
                if item.sku in self._lines:
                    self._lines[item.sku][1] += quantity
                else:
                    self._lines[item.sku] = [item, quantity]

            def remove_item(self, sku, quantity=1):
                line = self._lines.get(sku)
                if line is None:
                    raise KeyError(sku)
                line[1] -= quantity
                if line[1] <= 0:
                    del self._lines[sku]

            def is_empty(self):
                return not self._lines

            def item_count(self):
                return sum(q for _, q in self._lines.values())

            def subtotal(self):
                return round(sum(apply_discount(i.unit_price, q) for i, q in self._lines.values()), 2)

            def total(self):
                sub = self.subtotal()
                return round(sub + compute_tax(sub), 2)

            def total_weight(self):
                return sum(i.weight_kg * q for i, q in self._lines.values())
    '''),
    "inventory/shipping.py": _d('''
        """Shipping cost calculation."""

        ZONE_RATES = {"US": 5.0, "EU": 8.0, "ASIA": 12.0}
        FREE_SHIPPING_THRESHOLD = 100.0


        def shipping_cost(weight_kg, zone, order_total=0.0):
            """Delivery cost for a parcel; free above FREE_SHIPPING_THRESHOLD."""
            if order_total >= FREE_SHIPPING_THRESHOLD:
                return 0.0
            rate = ZONE_RATES[zone]
            return round(rate * max(weight_kg, 1.0), 2)
    '''),
    "inventory/utils/__init__.py": "",
    "inventory/utils/text.py": _d('''
        """Text helpers."""
        import re


        def slugify(text):
            """Lower-case, hyphen separated identifier."""
            text = text.strip().lower()
            text = re.sub(r"[^a-z0-9]+", "-", text)
            return text.strip("-")


        def truncate(text, limit):
            """Shorten text to at most `limit` characters, ending with an ellipsis when cut."""
            if limit < 4:
                raise ValueError("limit too small")
            return text if len(text) <= limit else text[: limit - 3] + "..."
    '''),
    "inventory/reports.py": _d('''
        """Human readable order reports."""
        from .utils.text import slugify, truncate


        def format_line(item, quantity):
            """One printable line for an order."""
            name = truncate(item.name, 20)
            return f"{name:<20} x{quantity:>3} {item.unit_price:>8.2f}"


        def summarize(cart):
            return {"items": cart.item_count(), "subtotal": cart.subtotal(), "total": cart.total()}


        def report_title(name):
            return f"report-{slugify(name)}"
    '''),
    "tests/test_pricing.py": _d('''
        from inventory.pricing import apply_discount, compute_tax


        def test_no_discount_below_threshold():
            assert apply_discount(10.0, 9) == 90.0


        def test_discount_applies_at_threshold():
            assert apply_discount(10.0, 10) == 90.0


        def test_discount_above_threshold():
            assert apply_discount(10.0, 20) == 180.0


        def test_tax():
            assert compute_tax(100.0) == 8.0
    '''),
    "tests/test_cart.py": _d('''
        from inventory.cart import Cart
        from inventory.models import Item


        def make():
            return Item("ABC-1234", "Widget", 10.0, 0.5)


        def test_add_and_count():
            c = Cart()
            c.add_item(make(), 3)
            assert c.item_count() == 3


        def test_remove_all_empties_cart():
            c = Cart()
            c.add_item(make(), 2)
            c.remove_item("ABC-1234", 2)
            assert c.is_empty()


        def test_total_includes_tax():
            c = Cart()
            c.add_item(make(), 2)
            assert c.total() == 21.6
    '''),
    "tests/test_validators.py": _d('''
        import pytest

        from inventory.validators import ValidationError, validate_quantity, validate_sku


        def test_valid_sku():
            assert validate_sku("ABC-1234") == "ABC-1234"


        def test_sku_rejects_trailing_garbage():
            with pytest.raises(ValidationError):
                validate_sku("ABC-1234-extra")


        def test_quantity_positive():
            assert validate_quantity(3) == 3


        def test_quantity_rejects_zero():
            with pytest.raises(ValidationError):
                validate_quantity(0)
    '''),
    "tests/test_shipping.py": _d('''
        from inventory.shipping import shipping_cost


        def test_us_rate():
            assert shipping_cost(2.0, "US") == 10.0


        def test_eu_rate():
            assert shipping_cost(1.0, "EU") == 8.0


        def test_free_shipping_over_threshold():
            assert shipping_cost(5.0, "ASIA", order_total=150.0) == 0.0
    '''),
    "tests/test_text.py": _d('''
        from inventory.utils.text import slugify, truncate


        def test_slugify():
            assert slugify("  Hello, World! ") == "hello-world"


        def test_truncate_respects_limit():
            assert len(truncate("x" * 50, 10)) == 10


        def test_truncate_short_unchanged():
            assert truncate("abc", 10) == "abc"
    '''),
    "tests/test_reports.py": _d('''
        from inventory.cart import Cart
        from inventory.models import Item
        from inventory.reports import format_line, report_title, summarize


        def test_format_line_shows_cents():
            assert "9.99" in format_line(Item("ABC-1234", "Widget", 9.99), 1)


        def test_summarize():
            c = Cart()
            c.add_item(Item("ABC-1234", "W", 10.0), 1)
            assert summarize(c)["items"] == 1


        def test_report_title():
            assert report_title("Q1 Sales") == "report-q1-sales"
    '''),
}


@dataclass
class TaskSpec:
    task_key: str
    description: str
    mutation_file: str
    old: str
    new: str
    relevant_tests: list[str]
    ground_truth_symbols: list[str]
    trace_frames: list[tuple[str, str, str, str]] = field(default_factory=list)  # (file, func, source line, locator)
    trace_exception: str = ""
    expected_failing: list[str] = field(default_factory=list)


TASK_SPECS: list[TaskSpec] = [
    TaskSpec(
        "synthetic-pricing-threshold",
        "Customers who order exactly ten units of a product are charged the full price; the bulk discount only starts at "
        "eleven units. The discount should apply as soon as the quantity reaches the bulk threshold.",
        "inventory/pricing.py", "if quantity >= BULK_THRESHOLD:", "if quantity > BULK_THRESHOLD:",
        ["tests/test_pricing.py::test_discount_applies_at_threshold"], ["apply_discount"],
        expected_failing=["tests/test_pricing.py::test_discount_applies_at_threshold"],
    ),
    TaskSpec(
        "synthetic-cart-remove-empty",
        "After removing every unit of the only product in a basket, the basket still does not report that it is empty.",
        "inventory/cart.py", "if line[1] <= 0:", "if line[1] < 0:",
        ["tests/test_cart.py::test_remove_all_empties_cart"], ["Cart.remove_item"],
        expected_failing=["tests/test_cart.py::test_remove_all_empties_cart"],
    ),
    TaskSpec(
        "synthetic-sku-trailing-text",
        "Product codes with trailing text such as ABC-1234-extra are accepted as valid, although only three letters, a dash "
        "and four digits should be allowed.",
        "inventory/validators.py", 'SKU_PATTERN = re.compile(r"^[A-Z]{3}-\\d{4}$")', 'SKU_PATTERN = re.compile(r"^[A-Z]{3}-\\d{4}")',
        ["tests/test_validators.py::test_sku_rejects_trailing_garbage"], ["SKU_PATTERN", "validate_sku"],
        expected_failing=["tests/test_validators.py::test_sku_rejects_trailing_garbage"],
    ),
    TaskSpec(
        "synthetic-truncate-length",
        "Shortening long product names for display returns strings that are three characters longer than the requested maximum length.",
        "inventory/utils/text.py", 'text[: limit - 3] + "..."', 'text[:limit] + "..."',
        ["tests/test_text.py::test_truncate_respects_limit"], ["truncate"],
        expected_failing=["tests/test_text.py::test_truncate_respects_limit"],
    ),
    TaskSpec(
        "synthetic-shipping-keyerror",
        "Calculating the delivery cost for a European order crashes instead of returning a price.",
        "inventory/shipping.py", '"EU": 8.0', '"EUR": 8.0',
        ["tests/test_shipping.py::test_eu_rate"], ["ZONE_RATES", "shipping_cost"],
        trace_frames=[
            ("tests/test_shipping.py", "test_eu_rate", 'assert shipping_cost(1.0, "EU") == 8.0', 'assert shipping_cost(1.0, "EU") == 8.0'),
            ("inventory/shipping.py", "shipping_cost", "rate = ZONE_RATES[zone]", "rate = ZONE_RATES[zone]"),
        ],
        trace_exception="KeyError: 'EU'",
        expected_failing=["tests/test_shipping.py::test_eu_rate"],
    ),
    TaskSpec(
        "synthetic-report-cents",
        "The printed order lines drop the cents, so an item priced 9.99 shows up as 10 in the report.",
        "inventory/reports.py", "{item.unit_price:>8.2f}", "{item.unit_price:>8.0f}",
        ["tests/test_reports.py::test_format_line_shows_cents"], ["format_line"],
        expected_failing=["tests/test_reports.py::test_format_line_shows_cents"],
    ),
]


def _line_of(text: str, needle: str) -> int:
    for i, line in enumerate(text.split("\n"), 1):
        if needle in line:
            return i
    return 1


def build_task_repo(dest: Path, spec: TaskSpec) -> dict:
    """Write base files, apply the mutation, return the task record (including ground truth + oracle fix)."""
    dest.mkdir(parents=True, exist_ok=True)
    files = dict(BASE_FILES)
    base_text = files[spec.mutation_file]
    if spec.old not in base_text:
        raise ValueError(f"mutation anchor not found for {spec.task_key}")
    buggy_text = base_text.replace(spec.old, spec.new, 1)
    files[spec.mutation_file] = buggy_text
    for rel, content in files.items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    trace = ""
    if spec.trace_frames:
        lines = ["Traceback (most recent call last):"]
        for fpath, func, src, locator in spec.trace_frames:
            text = files[fpath]
            lines.append(f'  File "{fpath}", line {_line_of(text, locator)}, in {func}')
            lines.append(f"    {src}")
        lines.append(spec.trace_exception)
        trace = "\n".join(lines)

    fix_diff = "".join(
        difflib.unified_diff(
            buggy_text.splitlines(keepends=True), base_text.splitlines(keepends=True),
            fromfile=f"a/{spec.mutation_file}", tofile=f"b/{spec.mutation_file}",
        )
    )
    return {
        "task_key": spec.task_key,
        "synthetic": True,
        "notice": SYNTHETIC_NOTICE,
        "description": spec.description,
        "stack_trace": trace,
        "relevant_tests": spec.relevant_tests,
        "expected_failing_tests": spec.expected_failing,
        "ground_truth_files": [spec.mutation_file],
        "ground_truth_symbols": spec.ground_truth_symbols,
        "oracle_fix_diff": fix_diff,  # PIPELINE TESTS ONLY: used by scripted model doubles, never by retrieval
    }


def materialize_suite(dest_root: Path) -> list[dict]:
    """Create one repository directory per task under dest_root and return task records (with `repo_path`)."""
    dest_root.mkdir(parents=True, exist_ok=True)
    records = []
    for spec in TASK_SPECS:
        repo_dir = dest_root / spec.task_key / "repo"
        rec = build_task_repo(repo_dir, spec)
        rec["repo_path"] = str(repo_dir)
        (dest_root / spec.task_key / "task.json").write_text(json.dumps({k: v for k, v in rec.items() if k != "repo_path"}, indent=2), encoding="utf-8")
        records.append(rec)
    return records


def load_task_records(root: Path) -> list[dict]:
    """Read previously materialized tasks (task.json beside each repo/)."""
    out = []
    for tj in sorted(root.glob("*/task.json")):
        rec = json.loads(tj.read_text(encoding="utf-8"))
        rec["repo_path"] = str(tj.parent / "repo")
        out.append(rec)
    return out
