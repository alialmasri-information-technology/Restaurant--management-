"""Saying things the way a person would.

The rule these tests are really protecting is the one in the module docstring:
be friendly right up to the point where friendliness costs precision, then stop.
"Just now" is better than a timestamp for something that happened a minute ago
and worse than useless for something from last August.
"""

from __future__ import annotations

import datetime as dt
import unittest

from app import db
from app.ui import phrasing

NOW = dt.datetime(2026, 9, 4, 17, 42, 0)


def at(*args) -> str:
    return dt.datetime(*args).strftime("%Y-%m-%d %H:%M:%S")


class RelativeTime(unittest.TestCase):
    def phrase(self, *args) -> str:
        return phrasing.relative_time(at(*args), NOW)

    def test_seconds_ago_is_just_now(self):
        self.assertEqual(self.phrase(2026, 9, 4, 17, 41, 40), "Just now")

    def test_a_minute_reads_as_a_minute(self):
        self.assertEqual(self.phrase(2026, 9, 4, 17, 41, 0), "A minute ago")

    def test_minutes_are_counted(self):
        self.assertEqual(self.phrase(2026, 9, 4, 17, 20, 0), "22 minutes ago")

    def test_an_hour_is_not_one_hours(self):
        self.assertEqual(self.phrase(2026, 9, 4, 16, 30, 0), "An hour ago")

    def test_hours_are_counted(self):
        self.assertEqual(self.phrase(2026, 9, 4, 9, 0, 0), "8 hours ago")

    def test_yesterday_keeps_its_clock(self):
        self.assertEqual(self.phrase(2026, 9, 3, 17, 40, 0), "Yesterday 17:40")

    def test_earlier_in_the_week_is_named_by_its_day(self):
        self.assertEqual(self.phrase(2026, 9, 2, 11, 0, 0), "Wednesday 11:00")

    def test_beyond_that_the_date_comes_back(self):
        """The point where friendliness would start hiding something."""
        self.assertEqual(self.phrase(2026, 8, 12, 11, 0, 0), "12 Aug 11:00")

    def test_another_year_keeps_its_year(self):
        self.assertEqual(self.phrase(2025, 8, 12, 11, 0, 0), "12 Aug 2025")

    def test_a_future_timestamp_is_stated_not_negated(self):
        self.assertEqual(self.phrase(2026, 9, 4, 18, 30, 0), "04 Sep 18:30")

    def test_a_clock_a_few_seconds_fast_still_reads_as_just_now(self):
        self.assertEqual(self.phrase(2026, 9, 4, 17, 42, 30), "Just now")

    def test_nothing_is_an_em_dash_not_the_word_none(self):
        self.assertEqual(phrasing.relative_time(None, NOW), "—")
        self.assertEqual(phrasing.relative_time("", NOW), "—")

    def test_the_empty_placeholder_can_be_chosen(self):
        self.assertEqual(phrasing.relative_time(None, NOW, empty="Never"), "Never")

    def test_unparseable_input_does_not_raise(self):
        self.assertEqual(phrasing.relative_time("not a date", NOW), "—")

    def test_a_datetime_object_works_as_well_as_a_string(self):
        self.assertEqual(
            phrasing.relative_time(dt.datetime(2026, 9, 4, 17, 20), NOW), "22 minutes ago"
        )

    def test_a_date_only_string_works(self):
        self.assertEqual(phrasing.relative_time("2026-09-03", NOW), "Yesterday 00:00")


class DayLabel(unittest.TestCase):
    def label(self, value) -> str:
        return phrasing.day_label(value, NOW)

    def test_today(self):
        self.assertEqual(self.label("2026-09-04 08:00:00"), "Today")

    def test_yesterday(self):
        self.assertEqual(self.label("2026-09-03"), "Yesterday")

    def test_within_the_week_is_a_weekday_name(self):
        self.assertEqual(self.label("2026-08-31"), "Monday")

    def test_older_is_a_date(self):
        self.assertEqual(self.label("2026-08-12"), "12 Aug")

    def test_another_year_keeps_its_year(self):
        self.assertEqual(self.label("2025-08-12"), "12 Aug 2025")

    def test_missing_uses_the_placeholder(self):
        self.assertEqual(phrasing.day_label(None, NOW, empty="Never"), "Never")


class Elapsed(unittest.TestCase):
    def test_under_a_minute(self):
        self.assertEqual(phrasing.elapsed(at(2026, 9, 4, 17, 41, 40), NOW), "a moment")

    def test_minutes(self):
        self.assertEqual(phrasing.elapsed(at(2026, 9, 4, 17, 20, 0), NOW), "22 minutes")

    def test_one_hour_is_singular(self):
        self.assertEqual(phrasing.elapsed(at(2026, 9, 4, 16, 30, 0), NOW), "1 hour")

    def test_hours(self):
        self.assertEqual(phrasing.elapsed(at(2026, 9, 4, 9, 0, 0), NOW), "8 hours")

    def test_days(self):
        self.assertEqual(phrasing.elapsed(at(2026, 9, 1, 9, 0, 0), NOW), "3 days")

    def test_a_clock_skew_never_produces_a_negative_age(self):
        self.assertEqual(phrasing.elapsed(at(2026, 9, 4, 18, 0, 0), NOW), "a moment")


class Greeting(unittest.TestCase):
    def test_morning(self):
        self.assertEqual(phrasing.greeting("Ali", dt.datetime(2026, 9, 4, 8)), "Good morning, Ali")

    def test_afternoon(self):
        self.assertEqual(phrasing.greeting("Ali", dt.datetime(2026, 9, 4, 14)), "Good afternoon, Ali")

    def test_evening(self):
        self.assertEqual(phrasing.greeting("Ali", dt.datetime(2026, 9, 4, 20)), "Good evening, Ali")

    def test_the_small_hours_get_their_own(self):
        self.assertEqual(phrasing.greeting("Ali", dt.datetime(2026, 9, 4, 3)), "You're up late, Ali")

    def test_only_the_first_name_is_used(self):
        greeting = phrasing.greeting("Ali Al Masri", dt.datetime(2026, 9, 4, 8))
        self.assertEqual(greeting, "Good morning, Ali")

    def test_no_name_means_no_dangling_comma(self):
        self.assertEqual(phrasing.greeting("", dt.datetime(2026, 9, 4, 8)), "Good morning")
        self.assertEqual(phrasing.greeting("   ", dt.datetime(2026, 9, 4, 8)), "Good morning")


class Counting(unittest.TestCase):
    def test_one_is_singular(self):
        self.assertEqual(phrasing.plural(1, "product"), "1 product")

    def test_more_than_one_is_plural(self):
        self.assertEqual(phrasing.plural(3, "product"), "3 products")

    def test_zero_is_plural(self):
        self.assertEqual(phrasing.plural(0, "sale"), "0 sales")

    def test_large_numbers_are_grouped(self):
        self.assertEqual(phrasing.plural(2000, "item"), "2,000 items")

    def test_an_irregular_plural_can_be_given(self):
        self.assertEqual(phrasing.plural(2, "person", "people"), "2 people")

    def test_the_verb_agrees(self):
        self.assertEqual(phrasing.verb(1, "is"), "is")
        self.assertEqual(phrasing.verb(2, "is"), "are")


class Listing(unittest.TestCase):
    def test_one(self):
        self.assertEqual(phrasing.listing(["a"]), "a")

    def test_two(self):
        self.assertEqual(phrasing.listing(["a", "b"]), "a and b")

    def test_three(self):
        self.assertEqual(phrasing.listing(["a", "b", "c"]), "a, b and c")

    def test_blanks_are_dropped(self):
        self.assertEqual(phrasing.listing(["a", "", "  ", "b"]), "a and b")

    def test_nothing_uses_the_placeholder(self):
        self.assertEqual(phrasing.listing([], empty="nobody"), "nobody")

    def test_the_joiner_can_be_changed(self):
        self.assertEqual(phrasing.listing(["a", "b"], joiner="or"), "a or b")


class Text(unittest.TestCase):
    def test_short_text_is_untouched(self):
        self.assertEqual(phrasing.truncate("Short", 20), "Short")

    def test_long_text_breaks_on_a_word(self):
        result = phrasing.truncate("one two three four five six", 12)
        self.assertTrue(result.endswith("…"))
        self.assertNotIn("thre…", result)

    def test_none_is_empty_not_the_word_none(self):
        self.assertEqual(phrasing.truncate(None), "")

    def test_a_missing_customer_is_a_walk_in(self):
        self.assertEqual(phrasing.name_or(None), "Walk-in")
        self.assertEqual(phrasing.name_or("  "), "Walk-in")
        self.assertEqual(phrasing.name_or("Cafe Nadia"), "Cafe Nadia")


class Truncation(unittest.TestCase):
    """A capped list has to admit it, and an uncapped one has to stay quiet."""

    def test_a_plain_list_says_nothing(self):
        self.assertEqual(phrasing.truncation_note(["a", "b"]), "")

    def test_a_list_that_fitted_says_nothing(self):
        rows = db.RowList(["a", "b"])
        rows.truncated = False
        self.assertEqual(phrasing.truncation_note(rows), "")

    def test_a_capped_list_says_how_far_it_got_and_what_to_do(self):
        rows = db.RowList(range(500))
        rows.truncated = True
        note = phrasing.truncation_note(rows)
        self.assertIn("first 500", note)
        self.assertIn("Search", note)
