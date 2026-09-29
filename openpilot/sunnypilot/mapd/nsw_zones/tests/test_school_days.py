# Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.
# This file is part of sunnypilot and is licensed under the MIT License.
# See the LICENSE.md file in the root directory for more details.
#
# FORK(NSW-ZONES): tests for school_days.py (ported from the P1 tools/test_nsw_school_days.py).
# Contains data from Transport for NSW, CC BY 4.0, modified.
"""Unit tests for the NSW school-zone day calendar."""

import copy
import datetime as dt
import json
import os
import tempfile
import unittest

from openpilot.sunnypilot.mapd.nsw_zones import school_days as sd

D = dt.date.fromisoformat
E, W = sd.EASTERN, sd.WESTERN

# Verbatim OP_CAL values from TfNSW SchoolZones.json (generated 2026-09-07).
OPCAL_N = (
  "<OperatingCalendar StartDate='2026-02-09' EndDate='2026-04-02' Recurrence='* * * 1-5 *'/>"
  + "<OperatingCalendar StartDate='2026-04-22' EndDate='2026-07-03' Recurrence='* * * 1-5 *'/>"
  + "<OperatingCalendar StartDate='2026-07-21' EndDate='2026-09-25' Recurrence='* * * 1-5 *'/>"
  + "<OperatingCalendar StartDate='2026-10-13' EndDate='2026-12-17' Recurrence='* * * 1-5 *'/>"
)
OPCAL_Y = OPCAL_N.replace("2026-02-09", "2026-02-02")


class KnownDates(unittest.TestCase):
  def check(self, day, expected, divisions=(E, W, None), reason=None):
    for div in divisions:
      st = sd.day_status(day, div)
      self.assertIs(st.zone_day, expected, f"{day} {div} -> {st}")
      if reason is not None:
        self.assertTrue((reason) in (st.reason), f"{day} {div} -> {st}")

  def test_term_weekday(self):
    self.check("2026-09-14", True, reason="term")  # Mon, Term 3 2026
    self.check("2026-03-10", True, reason="term")  # Tue, Term 1 2026 (both divisions)
    self.check("2026-09-25", True, reason="term")  # Fri, last day of Term 3
    self.check("2027-05-12", True, reason="term")  # Wed, Term 2 2027 (derived year)

  def test_school_holiday_weekday(self):
    self.check("2026-09-28", False, reason="school_holiday")  # Mon, spring holidays
    self.check("2026-07-08", False, reason="school_holiday")  # Wed, winter holidays
    self.check("2026-04-14", False, reason="school_holiday")  # Tue, autumn holidays
    self.check("2027-01-05", False, reason="school_holiday")  # Tue, summer 2026-27
    self.check("2027-10-05", False, reason="school_holiday")  # Tue, spring 2027

  def test_public_holiday_in_term(self):
    # Each of these weekdays lies INSIDE a published term range.
    cases = [
      ("2026-06-08", "King's Birthday"),  # Term 2 2026: 22 Apr - 3 Jul
      ("2026-04-27", "Anzac Day additional day"),  # Term 2 2026 (s.5 order)
      ("2027-03-26", "Good Friday"),  # Term 1 2027: 3 Feb - 9 Apr
      ("2027-03-29", "Easter Monday"),
      ("2027-06-14", "King's Birthday"),
    ]  # Term 2 2027: 29 Apr - 2 Jul
    doc = sd.build_json()
    in_term = {p["date"]: p["in_term"] for p in doc["public_holidays"]}
    for day, name in cases:
      self.check(day, False, reason="public_holiday: " + name)
      self.assertTrue(in_term[day][E] and in_term[day][W], day)

  def test_weekend(self):
    self.check("2026-09-19", False, reason="weekend")  # Sat inside Term 3
    self.check("2026-09-20", False, reason="weekend")  # Sun inside Term 3
    self.check("2027-03-27", False, reason="weekend")  # Easter Saturday inside Term 1 2027

  def test_development_days(self):
    for s in ("2026-01-27", "2026-01-28", "2026-01-29", "2026-01-30"):
      self.check(s, True, (E, None), "development_day")
      self.check(s, False, (W,), "school_holiday")
    for s in ("2026-02-03", "2026-02-04", "2026-02-05", "2026-02-06"):
      self.check(s, True, (W,), "development_day")
      self.check(s, True, (E,), "term")
    for s in ("2026-04-20", "2026-04-21", "2026-07-20", "2026-10-12", "2027-04-27", "2027-04-28", "2027-07-19", "2027-10-11"):
      self.check(s, True, reason="development_day")
    for s in ("2027-01-28", "2027-01-29", "2027-02-01", "2027-02-02"):
      self.check(s, True, (E,), "development_day")
      self.check(s, False, (W,), "school_holiday")
    for s in ("2027-02-04", "2027-02-05", "2027-02-08", "2027-02-09"):
      self.check(s, True, (W,), "development_day")

  def test_division_term1_boundaries(self):
    self.check("2026-02-02", True, (E, None))
    self.check("2026-02-02", False, (W,), "school_holiday")  # W summer holidays end Mon 2 Feb 2026
    self.check("2026-02-09", True)
    self.check("2027-02-03", True, (E,), "term")
    self.check("2027-02-03", False, (W,), "school_holiday")  # W summer holidays end Wed 3 Feb 2027
    self.check("2027-02-10", True, (W,), "term")

  def test_term_edges(self):
    self.check("2026-04-02", True)  # last day of Term 1
    self.check("2026-04-03", False, reason="Good Friday")
    self.check("2026-04-06", False, reason="Easter Monday")
    self.check("2026-04-07", False, reason="school_holiday")
    self.check("2026-04-22", True, reason="term")  # first student day of Term 2
    self.check("2026-12-17", True)
    self.check("2026-12-18", False, reason="school_holiday")
    self.check("2027-04-09", True)  # last day of Term 1 2027
    self.check("2027-04-26", False, reason="Anzac Day additional day")  # between holidays and dev days
    self.check("2027-12-20", True)  # Term 4 2027 ends on a Monday
    self.check("2027-12-21", False, reason="school_holiday")

  def test_bank_holiday_is_a_zone_day(self):
    self.check("2026-08-03", True, reason="term")
    self.check("2027-08-02", True, reason="term")

  def test_every_listed_public_holiday_is_off(self):
    for _name, a, b in sd.PUBLIC_HOLIDAYS:
      for s, y in ((a, 2026), (b, 2027)):
        if s:
          for d in sd.parse_days(s, y):
            self.check(d, False)

  def test_outside_coverage_is_none(self):
    self.check("2025-12-31", None, reason="before_coverage")
    self.check("2025-06-02", None)
    self.check("2029-03-01", None, reason="after_coverage")
    self.check("2026-01-01", False, reason="New Year")
    self.check("2026-01-02", False, reason="school_holiday")
    self.check("2028-01-28", False, (E, W, None))  # last published E holiday day
    self.check("2028-01-31", None, (E, None))
    self.check("2028-01-31", False, (W,), "school_holiday")  # W holidays run to 4 Feb 2028
    self.check("2028-02-04", False, (W,))
    self.check("2028-02-07", None, (W, None))
    self.check("2028-01-29", False, (W,), "weekend")  # Saturday inside W coverage
    self.check("2028-01-29", None, (E, None), "after_coverage")  # outside coverage is None even on a weekend
    self.check("2028-02-05", None, (W,))

  def test_counts_match_hand_counts(self):
    # Hand counts from the published tables, independent of the code:
    # E2026 48+53+50+49, W2026 43+53+50+49, E2027 50+48+50+51, W2027 45+48+50+51.
    cal = sd.get_calendar()

    def count(k, y):
      return sum(1 for d in cal.zone_days(k) if d.year == y)

    self.assertEqual(count(E, 2026), 200)
    self.assertEqual(count(W, 2026), 195)
    self.assertEqual(count(E, 2027), 199)
    self.assertEqual(count(W, 2027), 194)

  def test_union_is_conservative(self):
    cal = sd.get_calendar()
    d = D("2026-01-01")
    while d <= D("2028-01-28"):
      e, w, u = (cal.is_school_zone_day(d, k) for k in (E, W, None))
      self.assertEqual(u, True if (e or w) else (False if (e is False and w is False) else None), d)
      self.assertEqual(u, e, d)  # 2026-27: eastern days are a superset of western days
      d += dt.timedelta(days=1)

  def test_every_covered_day_is_classified(self):
    cal = sd.get_calendar()
    for k in (E, W):
      a, b = cal.coverage[k]
      d = a
      while d <= b:
        st = cal.status(d, k)
        self.assertIsNotNone(st.zone_day, (d, k))
        self.assertTrue(st.reason in ("term", "development_day", "weekend", "school_holiday") or st.reason.startswith("public_holiday: "), (d, st))
        d += dt.timedelta(days=1)

  def test_strict_mode(self):
    self.assertIsNone(sd.is_school_zone_day("2027-05-12", E, strict=True))  # derived year
    self.assertIs(sd.is_school_zone_day("2027-05-15", E, strict=True), False)  # a Saturday is still known
    self.assertIs(sd.is_school_zone_day("2026-09-14", E, strict=True), True)
    self.assertTrue(sd.day_status("2026-09-14", E).tfnsw_table)
    self.assertFalse(sd.day_status("2027-05-12", E).tfnsw_table)


class Api(unittest.TestCase):
  def test_input_types(self):
    for v in (dt.date(2026, 9, 14), dt.datetime(2026, 9, 14, 8, 30), "2026-09-14", "2026-09-14T08:30:00+10:00"):
      self.assertIs(sd.is_school_zone_day(v, E), True, v)
    self.assertRaises(ValueError, sd.is_school_zone_day, "2026-09-14", "Y")
    self.assertRaises(ValueError, sd.is_school_zone_day, "2026-09-14", "north")
    self.assertRaises(TypeError, sd.is_school_zone_day, 1789000000)

  def test_division_for_zone(self):
    self.assertEqual(sd.division_for_zone("Y"), W)
    self.assertEqual(sd.division_for_zone(" y "), W)
    self.assertEqual(sd.division_for_zone("N"), E)
    for v in (None, "", "X", 1):
      self.assertIsNone(sd.division_for_zone(v))

  def test_attribution(self):
    self.assertTrue(("Contains data from Transport for NSW, CC BY 4.0, modified") in (sd.ATTRIBUTION))


class Parser(unittest.TestCase):
  def test_forms(self):
    self.assertEqual(len(sd.parse_days("Tuesday 27 January to Friday 30 January", 2026)), 4)
    self.assertEqual(sd.parse_days("Monday 20 April and Tuesday 21 April", 2026), [D("2026-04-20"), D("2026-04-21")])
    self.assertEqual(sd.parse_days("Monday 12 October", 2026), [D("2026-10-12")])
    self.assertEqual(len(sd.parse_days("Monday 2 February to Thursday 2 April (inclusive)", 2026)), 60)
    r = sd.parse_days("Tuesday 21 December to Friday 28 January 2028", 2027)
    self.assertEqual((r[0], r[-1], len(r)), (D("2027-12-21"), D("2028-01-28"), 39))

  def test_rejects_wrong_weekday_and_garbage(self):
    self.assertRaises(ValueError, sd.parse_days, "Monday 3 February", 2026)  # 3 Feb 2026 is a Tuesday
    self.assertRaises(ValueError, sd.parse_days, "Friday 30 January to Tuesday 27 January", 2026)
    self.assertRaises(ValueError, sd.parse_days, "Term 1 starts soon", 2026)

  def test_builder_refuses_bad_source(self):
    saved = copy.deepcopy(sd.DOE_CALENDAR)
    try:
      sd.DOE_CALENDAR[2027][E]["development_days"][2] = "Tuesday 19 July"  # wrong weekday
      self.assertRaises(ValueError, sd.build_calendar)
      sd.DOE_CALENDAR.clear()
      sd.DOE_CALENDAR.update(copy.deepcopy(saved))
      sd.DOE_CALENDAR[2027][E]["holidays"].pop(2)  # winter holidays -> gap
      self.assertRaisesRegex(ValueError, "is in 0 of", sd.build_calendar)
      sd.DOE_CALENDAR.clear()
      sd.DOE_CALENDAR.update(copy.deepcopy(saved))
      sd.DOE_CALENDAR[2026][W]["development_days"][0] = "Tuesday 3 February"  # disagrees with TfNSW table
      self.assertRaisesRegex(ValueError, "TfNSW 2026 western development days", sd.build_calendar)
    finally:
      sd.DOE_CALENDAR.clear()
      sd.DOE_CALENDAR.update(saved)
    sd.build_calendar()


class OpCal(unittest.TestCase):
  def test_parse(self):
    r = sd.parse_op_cal(OPCAL_N)
    self.assertEqual(len(r), 4)
    self.assertEqual(r[0][:2], (D("2026-02-09"), D("2026-04-02")))
    self.assertRaises(ValueError, sd.op_cal_days, "<OperatingCalendar StartDate='2026-01-01' EndDate='2026-01-02' Recurrence='* * * * 0'/>")

  def test_eastern_record_is_swapped_and_incomplete(self):
    a = sd.audit_op_cal(OPCAL_N, E)
    missing = [s.split()[0] for s in a["missing"]]
    extra = [s.split()[0] for s in a["extra"]]
    for s in ("2026-02-02", "2026-02-06", "2026-01-27", "2026-04-20", "2026-07-20", "2026-10-12"):
      self.assertTrue((s) in (missing))
    self.assertEqual(extra, ["2026-04-27", "2026-06-08"])  # public holidays in term
    # Against WESTERN it only lacks development days: it is the Western term.
    b = sd.audit_op_cal(OPCAL_N, W)
    self.assertTrue(all("development_day" in s for s in b["missing"]))

  def test_western_record(self):
    a = sd.audit_op_cal(OPCAL_Y, W)
    self.assertEqual([s.split()[0] for s in a["extra"]], ["2026-02-02", "2026-04-27", "2026-06-08"])


class Json(unittest.TestCase):
  def test_round_trip(self):
    with tempfile.TemporaryDirectory() as t:
      p = os.path.join(t, "cal.json")
      sd.write_json(p)  # write_json itself re-checks every day
      with open(p, encoding="utf-8") as f:
        doc = json.load(f)
      self.assertTrue(("Contains data from Transport for NSW, CC BY 4.0, modified") in (doc["attribution"]))
      cal = sd.load_calendar(p)
      ref = sd.get_calendar()
      d = D("2025-12-01")
      while d <= D("2028-03-01"):
        for k in (E, W, None):
          self.assertEqual(cal.status(d, k), ref.status(d, k), (d, k))
        d += dt.timedelta(days=1)
      for k in (E, W):
        runs = doc["school_zone_day_ranges"][k]
        days = set()
        for a, b in runs:
          x = D(a)
          while x <= D(b):
            if x.weekday() < 5:
              days.add(x)
            x += dt.timedelta(days=1)
        self.assertEqual(days, set(ref.zone_days(k)))

  def test_json_import_refuses_other_documents(self):
    self.assertRaises(ValueError, sd.calendar_from_json, {"format": "other"})


if __name__ == "__main__":
  unittest.main(verbosity=1)
