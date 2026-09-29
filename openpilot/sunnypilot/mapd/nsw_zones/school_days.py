# Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.
# This file is part of sunnypilot and is licensed under the MIT License.
# See the LICENSE.md file in the root directory for more details.
#
# FORK(NSW-ZONES): the NSW school-zone day calendar (ported from the P1 tools/nsw_school_days.py).
# Contains data from Transport for NSW, CC BY 4.0, modified.
# Term dates (c) State of New South Wales (Department of Education), CC BY 4.0.
# Public holidays (c) State of New South Wales, CC BY 4.0 - www.nsw.gov.au.
# Not endorsed by Transport for NSW.
"""NSW school-zone day calendar.

    is_school_zone_day(day, division=None) -> True | False | None

True means the 40 km/h (or 30 km/h) school-zone signs are in force on that
local civil date, during the times shown on the sign. False means they are not
in force: a weekend, a public holiday or a government-school holiday. None
means the date is outside the published range. The caller chooses a
conservative default for None; this module never guesses.

THE RULE. TfNSW (school-zones page, "School zone days") says the zones are in
force on all notified school days. That excludes weekends, public holidays and
public school holidays, and includes school development (pupil-free) days.
Road Rules 2014 r.318(3-1) says the same thing the other way round: any day
other than a Saturday or Sunday, a public holiday, or "a day publicly
notified as a school holiday for government schools". The builder computes
both and refuses to build if they disagree on any day.

    zone day = (term day OR development day) AND Mon-Fri AND NOT public holiday

  * The Bank Holiday (first Monday in August) is NOT a public holiday (the
    nsw.gov.au table note says so), so it is a zone day.
  * Local event days (Public Holidays Act s.8) are not public holidays either,
    so zones operate on them.
  * The Anzac Day additional days, Mon 27 Apr 2026 and Mon 26 Apr 2027, ARE
    whole-State public holidays declared under s.5. 27 Apr 2026 falls inside
    Term 2, so zones are off that day.

DIVISIONS. TfNSW publishes separate Term 1 dates for the Eastern and Western
divisions. DoE defines the Western division ("late start schools") as a named
list of schools in the far west. In TfNSW's SchoolZones.json the per-zone
field LATE_OPENING_SCHOOL marks it: 'Y' = Western, 'N' = Eastern (the
dataset PDF says Term 1 differs between the two). Use
division_for_zone(rec['LATE_OPENING_SCHOOL']). If the division is unknown,
pass None. None means the union, which is a zone day if either division
says so; for 2026-27 that is exactly the Eastern calendar, because Eastern
days are a superset of Western days. The Sydney default is Eastern.

DO NOT use TfNSW's OP_CAL field for days. In the 2026 file its Term 1 start
is swapped between the divisions in every record. It also omits development
days and does not exclude public holidays. See audit_op_cal().

The date must be the zone's LOCAL civil date. That is Australia/Sydney
everywhere except the Broken Hill area (Australia/Broken_Hill, 30 min behind),
and the two always share a date inside any school-zone window.

Pure Python standard library, no network access at runtime. The calendar is
built from the verbatim source strings embedded below, and every string is
checked against its weekday name. build_index.py embeds the same calendar as JSON
calendar exported for the index builder and the device; load it with
load_calendar(path).

CLI:
  python school_days.py --out nsw_school_days.json   # validate + write the JSON export
  python school_days.py --check 2026-09-14 [--division eastern|western]
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
from collections import namedtuple
from collections.abc import Iterable, Mapping
from typing import Any

FORMAT = "nsw_school_days"
FORMAT_VERSION = 1
EASTERN = "eastern"
WESTERN = "western"
DIVISIONS = (EASTERN, WESTERN)
SOURCES_RETRIEVED = "2026-09-29"

ATTRIBUTION = (
  "Contains data from Transport for NSW, CC BY 4.0, modified. "
  + "Term dates (c) State of New South Wales (Department of Education), CC BY 4.0. "
  + "Public holidays (c) State of New South Wales, CC BY 4.0 - www.nsw.gov.au. "
  + "Not endorsed by Transport for NSW."
)

# ---------------------------------------------------------------------------
# SOURCE DATA - verbatim date strings, retrieved 2026-09-29. Edit ONLY by
# pasting new text from the sources; the parser checks every weekday name.
# ---------------------------------------------------------------------------

URL_TFNSW = "https://www.transport.nsw.gov.au/roadsafety/community/schools/school-zones"
URL_DOE_2026 = "https://education.nsw.gov.au/schooling/calendars/2026"
URL_DOE_2027 = "https://education.nsw.gov.au/schooling/calendars/2027"
URL_DOE_FUTURE = "https://education.nsw.gov.au/schooling/calendars/future-and-past-nsw-term-and-vacation-dates"
URL_DOE_LATE = "https://education.nsw.gov.au/schooling/calendars/late-start-schools"
URL_NSW_PH = "https://www.nsw.gov.au/about-nsw/public-holidays"
URL_NSW_ANZAC = "https://www.nsw.gov.au/ministerial-releases/minns-labor-government-announces-extra-public-holiday-year"
URL_FWO_ANZAC = "https://www.fairwork.gov.au/newsroom/news/anzac-day-public-holidays"
URL_ROAD_RULES = "https://legislation.nsw.gov.au/view/whole/html/inforce/current/sl-2014-0758"  # codespell:ignore inforce
URL_PH_ACT = "https://legislation.nsw.gov.au/view/whole/html/inforce/current/act-2010-115"  # codespell:ignore inforce
URL_TFNSW_SZ_PDF = "schoolzones_v1.0.pdf (TfNSW Open Data Hub, Speed Zones dataset, school zones resource)"

# TfNSW school-zones page, "Notified school days" table (2026 is the only year
# on the page as retrieved). Terms carry "(inclusive)" on the page.
TFNSW_TABLE: dict[int, dict[str, dict[str, list[str]]]] = {
  2026: {
    EASTERN: {
      "terms": [
        "Monday 2 February to Thursday 2 April (inclusive)",  # Term 1 - Eastern Division NSW
        "Wednesday 22 April to Friday 3 July (inclusive)",  # Term 2 (Eastern and Western)
        "Tuesday 21 July to Friday 25 September (inclusive)",  # Term 3 (Eastern and Western)
        "Tuesday 13 October to Thursday 17 December (inclusive)",  # Term 4 (Eastern and Western)
      ],
      "development_days": [
        "Tuesday 27 January to Friday 30 January",
        "Monday 20 April and Tuesday 21 April",
        "Monday 20 July",
        "Monday 12 October",
      ],
    },
    WESTERN: {
      "terms": [
        "Monday 9 February to Thursday 2 April (inclusive)",  # Term 1 - Western Division NSW
        "Wednesday 22 April to Friday 3 July (inclusive)",
        "Tuesday 21 July to Friday 25 September (inclusive)",
        "Tuesday 13 October to Thursday 17 December (inclusive)",
      ],
      "development_days": [
        "Tuesday 3 February to Friday 6 February",
        "Monday 20 April and Tuesday 21 April",
        "Monday 20 July",
        "Monday 12 October",
      ],
    },
  },
}

# NSW Department of Education term-dates pages ("First to last days for
# students", "School development days", "School holidays"), per division.
DOE_CALENDAR: dict[int, dict[str, Any]] = {
  2026: {
    "url": URL_DOE_2026,
    "page_last_updated": "2026-08-12",
    EASTERN: {
      "terms": [
        "Monday 2 February to Thursday 2 April",
        "Wednesday 22 April to Friday 3 July",
        "Tuesday 21 July to Friday 25 September",
        "Tuesday 13 October to Thursday 17 December",
      ],
      "development_days": [
        "Tuesday 27 January to Friday 30 January",
        "Monday 20 April and Tuesday 21 April",
        "Monday 20 July",
        "Monday 12 October",
      ],
      "holidays": [
        "Monday 22 December 2025 to Monday 26 January 2026",  # Summer 2025-26
        "Tuesday 7 April to Friday 17 April",  # Autumn
        "Monday 6 July to Friday 17 July",  # Winter
        "Monday 28 September to Friday 9 October",  # Spring
        "Friday 18 December 2026 to Wednesday 27 January 2027",  # Summer 2026-27
      ],
    },
    WESTERN: {
      "terms": [
        "Monday 9 February to Thursday 2 April",
        "Wednesday 22 April to Friday 3 July",
        "Tuesday 21 July to Friday 25 September",
        "Tuesday 13 October to Thursday 17 December",
      ],
      "development_days": [
        "Tuesday 3 February to Friday 6 February",
        "Monday 20 April and Tuesday 21 April",
        "Monday 20 July",
        "Monday 12 October",
      ],
      "holidays": [
        "Monday 22 December 2025 to Monday 2 February 2026",
        "Tuesday 7 April to Friday 17 April",
        "Monday 6 July to Friday 17 July",
        "Monday 28 September to Friday 9 October",
        "Friday 18 December 2026 to Wednesday 3 February 2027",
      ],
    },
  },
  2027: {
    "url": URL_DOE_2027,
    "page_last_updated": "2026-08-13",
    EASTERN: {
      "terms": [
        "Wednesday 3 February to Friday 9 April",
        "Thursday 29 April to Friday 2 July",
        "Tuesday 20 July to Friday 24 September",
        "Tuesday 12 October to Monday 20 December",
      ],
      "development_days": [
        "Thursday 28 January to Tuesday 2 February",
        "Tuesday 27 April and Wednesday 28 April",
        "Monday 19 July",
        "Monday 11 October",
      ],
      "holidays": [
        "Friday 18 December 2026 to Wednesday 27 January 2027",  # Summer 2026-27
        "Monday 12 April to Friday 23 April",  # Autumn
        "Monday 5 July to Friday 16 July",  # Winter
        "Monday 27 September to Friday 8 October",  # Spring
        "Tuesday 21 December to Friday 28 January 2028",  # Summer 2027-28
      ],
    },
    WESTERN: {
      "terms": [
        "Wednesday 10 February to Friday 9 April",
        "Thursday 29 April to Friday 2 July",
        "Tuesday 20 July to Friday 24 September",
        "Tuesday 12 October to Monday 20 December",
      ],
      "development_days": [
        "Thursday 4 February to Tuesday 9 February",
        "Tuesday 27 April and Wednesday 28 April",
        "Monday 19 July",
        "Monday 11 October",
      ],
      "holidays": [
        "Friday 18 December 2026 to Wednesday 3 February 2027",
        "Monday 12 April to Friday 23 April",
        "Monday 5 July to Friday 16 July",
        "Monday 27 September to Friday 8 October",
        "Tuesday 21 December to Friday 4 February 2028",
      ],
    },
  },
}

# DoE "Future and past" page, 2027: term ranges that INCLUDE development days.
# Used only as an independent cross-check of the 2027 page.
DOE_FUTURE_2027: dict[str, list[str]] = {
  EASTERN: [
    "Thursday 28 January to Friday 9 April",  # Term 1 (Eastern)
    "Tuesday 27 April to Friday 2 July",  # Term 2
    "Monday 19 July to Friday 24 September",  # Term 3
    "Monday 11 October to Monday 20 December",  # Term 4
  ],
  WESTERN: [
    "Thursday 4 February to Friday 9 April",  # Term 1 (Western)
    "Tuesday 27 April to Friday 2 July",
    "Monday 19 July to Friday 24 September",
    "Monday 11 October to Monday 20 December",
  ],
}

# nsw.gov.au "NSW public holidays 2026 to 2027" table: (name, 2026, 2027).
PUBLIC_HOLIDAYS: list[tuple[str, str | None, str | None]] = [
  ("New Year's Day", "Thursday 1 January 2026", "Friday 1 January 2027"),
  ("Australia Day", "Monday 26 January 2026", "Tuesday 26 January 2027"),
  ("Good Friday", "Friday 3 April 2026", "Friday 26 March 2027"),
  ("Easter Saturday", "Saturday 4 April 2026", "Saturday 27 March 2027"),
  ("Easter Sunday", "Sunday 5 April 2026", "Sunday 28 March 2027"),
  ("Easter Monday", "Monday 6 April 2026", "Monday 29 March 2027"),
  ("Anzac Day", "Saturday 25 April 2026", "Sunday 25 April 2027"),
  # s.5 order, whole State (NSW Premier's release 15 Feb 2026; Fair Work Ombudsman).
  ("Anzac Day additional day", "Monday 27 April 2026", "Monday 26 April 2027"),
  ("King's Birthday", "Monday 8 June 2026", "Monday 14 June 2027"),
  ("Labour Day", "Monday 5 October 2026", "Monday 4 October 2027"),  # codespell:ignore labour
  ("Christmas Day", "Friday 25 December 2026", "Saturday 25 December 2027"),
  ("Christmas Day additional day", None, "Monday 27 December 2027"),
  ("Boxing Day", "Saturday 26 December 2026", "Sunday 26 December 2027"),
  ("Boxing Day additional day", "Monday 28 December 2026", "Tuesday 28 December 2027"),
]
PUBLIC_HOLIDAY_YEARS = (2026, 2027)

# Listed in the same table but "not a declared public holiday" (table note 1):
# schools are open, so the zones ARE in force.
NOT_PUBLIC_HOLIDAYS: list[tuple[str, str | None, str | None]] = [
  ("Bank Holiday", "Monday 3 August 2026", "Monday 2 August 2027"),
]

# Coverage starts where the public-holiday table starts. It ends on the last
# published day of the last published summer holidays (end of the 2027 data).
COVERAGE_FIRST = _dt.date(2026, 1, 1)

SOURCES: list[dict[str, Any]] = [
  {
    "id": "tfnsw_school_zones",
    "url": URL_TFNSW,
    "publisher": "Transport for NSW",
    "retrieved": SOURCES_RETRIEVED,
    "used_for": "The rule: zones are in force on notified school days (term plus development "
    + "days), excluding weekends, public holidays and public school holidays. "
    + "Also the authoritative 2026 table for both divisions.",
    "quote": "40km/h school zones are in force on all notified school days.",
    "dates": "TFNSW_TABLE[2026] (verbatim)",
  },
  {
    "id": "road_rules_2014_r318_3_1",
    "url": URL_ROAD_RULES,
    "publisher": "NSW Parliamentary Counsel's Office",
    "retrieved": SOURCES_RETRIEVED,
    "used_for": "Legal definition of school days, used as the cross-check: any day other than "
    + "Sat/Sun, a public holiday for the place, or a notified government-school holiday. "
    + "r.23 note: school days, see rule 318(3-1). Dictionary: public holiday for a place "
    + "= a public holiday at the place under NSW law.",
    "quote": "a day publicly notified as a school holiday for government schools",
  },
  {
    "id": "doe_2026",
    "url": URL_DOE_2026,
    "publisher": "NSW Department of Education",
    "retrieved": SOURCES_RETRIEVED,
    "page_last_updated": "2026-08-12",
    "used_for": "2026 terms, development days and school holidays per division (the terms and development days are identical to TfNSW's 2026 table; asserted).",
    "dates": "DOE_CALENDAR[2026] (verbatim)",
  },
  {
    "id": "doe_2027",
    "url": URL_DOE_2027,
    "publisher": "NSW Department of Education",
    "retrieved": SOURCES_RETRIEVED,
    "page_last_updated": "2026-08-13",
    "used_for": "2027 terms, development days and school holidays per division, and the summer "
    + "2027-28 holidays. TfNSW had not published a 2027 table when retrieved, so 2027 "
    + "zone days are derived from this page with TfNSW's rule.",
    "dates": "DOE_CALENDAR[2027] (verbatim)",
  },
  {
    "id": "doe_future",
    "url": URL_DOE_FUTURE,
    "publisher": "NSW Department of Education",
    "retrieved": SOURCES_RETRIEVED,
    "page_last_updated": "2026-08-17",
    "used_for": "Independent cross-check of 2027 (its term ranges include development days). "
    + "It also lists 2028-2030 ranges, which are NOT used because the 2028+ public "
    + "holidays are not yet published.",
    "dates": "DOE_FUTURE_2027 (verbatim)",
  },
  {
    "id": "doe_late_start",
    "url": URL_DOE_LATE,
    "publisher": "NSW Department of Education",
    "retrieved": SOURCES_RETRIEVED,
    "page_last_updated": "2026-07-17",
    "used_for": "Definition of the Western division: a named list of 117 'late start' public "
    + "schools in the far west (as of July 2026). Cross-checked against LATE_OPENING_SCHOOL.",
  },
  {
    "id": "nsw_public_holidays",
    "url": URL_NSW_PH,
    "publisher": "NSW Government",
    "retrieved": SOURCES_RETRIEVED,
    "used_for": "Public holidays 2026-2027, including the Anzac Day additional days, and the note "
    + "that the Bank Holiday is not a declared public holiday. The page's older FAQ "
    + "still says Anzac Day is observed only on 25 April; the table and the s.5 order "
    + "supersede it.",
    "dates": "PUBLIC_HOLIDAYS / NOT_PUBLIC_HOLIDAYS (verbatim)",
  },
  {
    "id": "nsw_anzac_release",
    "url": URL_NSW_ANZAC,
    "publisher": "NSW Government (Premier), 15 February 2026",
    "retrieved": SOURCES_RETRIEVED,
    "used_for": "Confirms the additional public holiday on the Monday after Anzac Day in 2026 and 2027.",
  },
  {
    "id": "fwo_anzac",
    "url": URL_FWO_ANZAC,
    "publisher": "Fair Work Ombudsman",
    "retrieved": SOURCES_RETRIEVED,
    "used_for": "Second confirmation: NSW 2026 has Monday 27 April as an additional public holiday for Anzac Day.",
  },
  {
    "id": "public_holidays_act_2010",
    "url": URL_PH_ACT,
    "publisher": "NSW Parliamentary Counsel's Office",
    "retrieved": SOURCES_RETRIEVED,
    "used_for": "s.4(g): Anzac Day is 25 April, with no weekend substitute in the Act, so the "
    + "Mondays come from s.5 orders. s.5 allows part-State holidays; s.8 local event days "
    + "are not public holidays.",
  },
  {
    "id": "tfnsw_schoolzones_pdf",
    "url": URL_TFNSW_SZ_PDF,
    "publisher": "Transport for NSW",
    "retrieved": "2026-09-29 (local copy S:/OP/nsw-speedzones/schoolzones_v1.0.pdf)",
    "used_for": "LATE_OPENING_SCHOOL Y/N: Term 1 differs between Western and Eastern. "
    + "OP_CAL Recurrence '* * * 1-5 *' means Monday to Friday every week. New term "
    + "dates are populated during the Term 4 holidays.",
  },
]

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = {
  m: i for i, m in enumerate(("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)
}
_DATE_RE = re.compile(
  r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+(\d{1,2})\s+"
  + r"(January|February|March|April|May|June|July|August|September|October|November|December)"
  + r"(?:\s+(\d{4}))?"
)


def _parse_one(m: re.Match[str], year: int) -> _dt.date:
  wd, d, mon, y = m.groups()
  day = _dt.date(int(y) if y else year, _MONTHS[mon], int(d))
  if _WEEKDAYS[day.weekday()] != wd:
    raise ValueError(f"{m.group(0)!r}: {day} is a {_WEEKDAYS[day.weekday()]}, not a {wd}")
  return day


def parse_days(text: str, year: int) -> list[_dt.date]:
  """Parse 'Monday 2 February to Thursday 2 April', 'A and B' or a single date.

  A date without a year takes `year`. Every weekday name is checked, and a
  mismatch raises ValueError. Ranges are inclusive and include weekends;
  the caller filters them out.
  """
  t = re.sub(r"\s*\(inclusive\)\s*$", "", text.strip().rstrip("."))
  ms = list(_DATE_RE.finditer(t))
  shape = re.sub(r"\s+", " ", _DATE_RE.sub("#", t)).strip()
  ds = [_parse_one(m, year) for m in ms]
  if shape == "#":
    return ds
  if shape == "# and #":
    return ds
  if shape == "# to #":
    a, b = ds
    if b < a:
      raise ValueError(f"{text!r}: range ends before it starts")
    return [a + _dt.timedelta(days=i) for i in range((b - a).days + 1)]
  raise ValueError(f"unparsable date phrase {text!r}")


def _days(phrases: Iterable[str], year: int) -> set[_dt.date]:
  out: set[_dt.date] = set()
  for p in phrases:
    out.update(parse_days(p, year))
  return out


def _weekdays(days: Iterable[_dt.date]) -> set[_dt.date]:
  return {d for d in days if d.weekday() < 5}


# ---------------------------------------------------------------------------
# The calendar
# ---------------------------------------------------------------------------

DayStatus = namedtuple("DayStatus", "zone_day reason division tfnsw_table")
DayStatus.__doc__ = """zone_day: True/False/None; reason: 'term' | 'development_day' | 'weekend' |
'public_holiday: <name>' | 'school_holiday' | 'before_coverage' | 'after_coverage'
(prefixed 'union ' when division was None); division: 'eastern'/'western'/None;
tfnsw_table: True if TfNSW's own table covers that year (False = derived from DoE
with TfNSW's rule)."""


def _as_date(day) -> _dt.date:
  if isinstance(day, _dt.datetime):
    return day.date()  # caller's responsibility: a LOCAL datetime
  if isinstance(day, _dt.date):
    return day
  if isinstance(day, str):
    return _dt.date.fromisoformat(day.strip()[:10])
  raise TypeError(f"expected date, datetime or 'YYYY-MM-DD', got {day!r}")


def _as_division(division) -> str | None:
  if division is None:
    return None
  d = str(division).strip().lower()
  if d in DIVISIONS:
    return d
  raise ValueError(f"division must be 'eastern', 'western' or None, got {division!r}")


def division_for_zone(late_opening_school) -> str | None:
  """TfNSW SchoolZones.json LATE_OPENING_SCHOOL -> division ('Y' western, 'N' eastern, else None)."""
  f = str(late_opening_school or "").strip().upper()
  return WESTERN if f == "Y" else EASTERN if f == "N" else None


class SchoolDayCalendar:
  """Immutable per-division sets of school-zone days (date ordinals)."""

  def __init__(
    self,
    zone_days: Mapping[str, Iterable[_dt.date]],
    development_days: Mapping[str, Iterable[_dt.date]],
    public_holidays: Mapping[_dt.date, str],
    coverage: Mapping[str, tuple[_dt.date, _dt.date]],
    tfnsw_table_years: Iterable[int],
    meta: dict | None = None,
  ):
    self._zone = {k: frozenset(d.toordinal() for d in v) for k, v in zone_days.items()}
    self._dev = {k: frozenset(d.toordinal() for d in v) for k, v in development_days.items()}
    self._ph = {d.toordinal(): n for d, n in public_holidays.items()}
    self._cov = {k: (a.toordinal(), b.toordinal()) for k, (a, b) in coverage.items()}
    self.coverage = dict(coverage)
    self.tfnsw_table_years = frozenset(tfnsw_table_years)
    self.meta = dict(meta or {})
    for k in DIVISIONS:
      if k not in self._zone or k not in self._cov:
        raise ValueError(f"calendar lacks division {k!r}")

  def status(self, day, division=None, strict: bool = False) -> DayStatus:
    d = _as_date(day)
    div = _as_division(division)
    if div is None:
      e = self.status(d, EASTERN, strict)
      w = self.status(d, WESTERN, strict)
      if e.zone_day or w.zone_day:
        pick = e if e.zone_day else w
      elif e.zone_day is False and w.zone_day is False:
        pick = e
      else:
        pick = e if e.zone_day is None else w
      return DayStatus(pick.zone_day, "union " + pick.reason, None, e.tfnsw_table and w.tfnsw_table)
    o = d.toordinal()
    first, last = self._cov[div]
    tf = d.year in self.tfnsw_table_years
    if o < first:
      return DayStatus(None, "before_coverage", div, tf)
    if o > last:
      return DayStatus(None, "after_coverage", div, tf)
    if d.weekday() >= 5:
      return DayStatus(False, "weekend", div, tf)
    if strict and not tf:
      return DayStatus(None, "not_in_tfnsw_table", div, tf)
    if o in self._zone[div]:
      return DayStatus(True, "development_day" if o in self._dev[div] else "term", div, tf)
    if o in self._ph:
      return DayStatus(False, "public_holiday: " + self._ph[o], div, tf)
    return DayStatus(False, "school_holiday", div, tf)

  def is_school_zone_day(self, day, division=None, strict: bool = False) -> bool | None:
    """True/False, or None outside the published range.

    division: 'eastern', 'western' or None (unknown: the conservative union).
    strict=True also returns None for weekdays in years that TfNSW's own
    table does not yet cover (2027 is derived from DoE).
    """
    return self.status(day, division, strict).zone_day

  def zone_days(self, division: str) -> list[_dt.date]:
    div = _as_division(division)
    if div is None:
      raise ValueError("zone_days needs a division")
    return sorted(_dt.date.fromordinal(o) for o in self._zone[div])


# ---------------------------------------------------------------------------
# Build from the embedded sources, with validation
# ---------------------------------------------------------------------------


def _source_sets():
  """Parse all embedded strings -> per-division term/dev/holiday sets and PH map."""
  terms = {k: set() for k in DIVISIONS}
  dev = {k: set() for k in DIVISIONS}
  hol = {k: set() for k in DIVISIONS}
  term_starts = {k: {} for k in DIVISIONS}
  for year, page in DOE_CALENDAR.items():
    for k in DIVISIONS:
      p = page[k]
      t = _days(p["terms"], year)
      terms[k] |= t
      term_starts[k][year] = [parse_days(s, year)[0] for s in p["terms"]]
      dev[k] |= _days(p["development_days"], year)
      hol[k] |= _days(p["holidays"], year)
  ph: dict[_dt.date, str] = {}
  for name, a, b in PUBLIC_HOLIDAYS:
    for s, y in ((a, 2026), (b, 2027)):
      if s:
        for d in parse_days(s, y):
          if d in ph:
            raise ValueError(f"duplicate public holiday {d}")
          ph[d] = name
  not_ph: dict[_dt.date, str] = {}
  for name, a, b in NOT_PUBLIC_HOLIDAYS:
    for s, y in ((a, 2026), (b, 2027)):
      if s:
        for d in parse_days(s, y):
          not_ph[d] = name
  return terms, dev, hol, ph, not_ph, term_starts


def build_calendar(return_checks: bool = False):
  """Build the calendar from the embedded sources. Raises ValueError on any inconsistency."""
  terms, dev, hol, ph, not_ph, term_starts = _source_sets()
  checks: dict[str, object] = {"weekday_names_match_dates": True}

  def need(cond, msg):
    if not cond:
      raise ValueError("calendar check failed: " + msg)

  # The TfNSW 2026 table must equal DoE 2026 (terms and development days).
  for year, tab in TFNSW_TABLE.items():
    for k in DIVISIONS:
      need(_days(tab[k]["terms"], year) == _days(DOE_CALENDAR[year][k]["terms"], year), f"TfNSW {year} {k} terms != DoE")
      need(_days(tab[k]["development_days"], year) == _days(DOE_CALENDAR[year][k]["development_days"], year), f"TfNSW {year} {k} development days != DoE")
  checks["tfnsw_table_equals_doe"] = sorted(TFNSW_TABLE)

  # The DoE future-page 2027 ranges (development days included) must equal the 2027 page.
  for k in DIVISIONS:
    fut = _weekdays(_days(DOE_FUTURE_2027[k], 2027))
    det = _weekdays(_days(DOE_CALENDAR[2027][k]["terms"], 2027) | _days(DOE_CALENDAR[2027][k]["development_days"], 2027))
    need(fut == det, f"DoE future page 2027 {k} != 2027 page")
  checks["doe_future_page_2027_equals_2027_page"] = True

  # A holiday range printed on two pages (summer 2026-27) must agree.
  for k in DIVISIONS:
    s26 = [s for s in DOE_CALENDAR[2026][k]["holidays"] if "2027" in s]
    s27 = [s for s in DOE_CALENDAR[2027][k]["holidays"] if "2026" in s]
    need(
      len(s26) == 1 and len(s27) == 1 and parse_days(s26[0], 2026) == parse_days(s27[0], 2027), f"summer 2026-27 {k} differs between the 2026 and 2027 pages"
    )
  checks["summer_2026_27_same_on_both_pages"] = True

  coverage: dict[str, tuple[_dt.date, _dt.date]] = {}
  zone: dict[str, set[_dt.date]] = {}
  for k in DIVISIONS:
    school = _weekdays(terms[k] | dev[k])
    need(not (_weekdays(terms[k]) & dev[k]), f"{k} development days overlap student days")
    last = max(terms[k] | dev[k] | hol[k])
    first = COVERAGE_FIRST
    coverage[k] = (first, last)
    # Every weekday is exactly one of: a school day (term or development day)
    # or a notified school holiday. A day outside both is allowed only if it
    # is a public holiday (e.g. Good Friday 2026 between Term 1 and the autumn holidays).
    d = first
    while d <= last:
      if d.weekday() < 5:
        n = (d in school) + (d in hol[k])
        need(n == 1 or (n == 0 and d in ph), f"{k} {d} is in {n} of term/holiday")
      d += _dt.timedelta(days=1)
    # The public-holiday table must cover every year that has school days.
    need({x.year for x in school if first <= x <= last} <= set(PUBLIC_HOLIDAY_YEARS), f"public holidays not published for every year with school days ({k})")
    z_tfnsw = {x for x in school if first <= x <= last and x not in ph}
    # Road Rules r.318(3-1): weekdays minus public holidays minus notified school holidays.
    z_legal = set()
    d = first
    while d <= last:
      if d.weekday() < 5 and d not in ph and d not in hol[k]:
        z_legal.add(d)
      d += _dt.timedelta(days=1)
    need(z_tfnsw == z_legal, f"{k}: TfNSW rule and Road Rules r.318(3-1) disagree on {sorted(z_tfnsw ^ z_legal)[:5]}")
    zone[k] = z_tfnsw
  checks["every_weekday_classified_once"] = True
  checks["tfnsw_rule_equals_road_rules_318_3_1"] = True

  for d, name in not_ph.items():
    for k in DIVISIONS:
      need(d in zone[k], f"{name} {d} should be a zone day in {k}")
  checks["bank_holiday_is_zone_day"] = True
  checks["eastern_superset_of_western"] = zone[WESTERN] <= zone[EASTERN]

  ph_in_cov = {d: n for d, n in ph.items() if COVERAGE_FIRST <= d <= max(c[1] for c in coverage.values())}
  cal = SchoolDayCalendar(
    zone_days=zone,
    development_days={k: {d for d in dev[k] if d in zone[k]} for k in DIVISIONS},
    public_holidays=ph_in_cov,
    coverage=coverage,
    tfnsw_table_years=TFNSW_TABLE.keys(),
    meta={"source": "embedded", "sources_retrieved": SOURCES_RETRIEVED},
  )
  if return_checks:
    return cal, checks, {"terms": terms, "dev": dev, "hol": hol, "ph": ph, "not_ph": not_ph, "term_starts": term_starts}
  return cal


_DEFAULT: SchoolDayCalendar | None = None


def get_calendar() -> SchoolDayCalendar:
  global _DEFAULT
  if _DEFAULT is None:
    _DEFAULT = build_calendar()
  return _DEFAULT


def is_school_zone_day(day, division=None, strict: bool = False) -> bool | None:
  """Are NSW school zones in force on this local date? True/False, None if unknown.

  division: 'eastern' (Sydney; LATE_OPENING_SCHOOL 'N'), 'western'
  (LATE_OPENING_SCHOOL 'Y'), or None for the conservative union.
  """
  return get_calendar().is_school_zone_day(day, division, strict)


def day_status(day, division=None, strict: bool = False) -> DayStatus:
  return get_calendar().status(day, division, strict)


# ---------------------------------------------------------------------------
# TfNSW OP_CAL (SchoolZones.json): parse and audit. For cross-checking only.
# ---------------------------------------------------------------------------

_OPCAL_RE = re.compile(
  r"<OperatingCalendar\s+StartDate=['\"](\d{4}-\d{2}-\d{2})['\"]\s+" + r"EndDate=['\"](\d{4}-\d{2}-\d{2})['\"]\s+Recurrence=['\"]([^'\"]*)['\"]\s*/>"
)


def parse_op_cal(op_cal: str) -> list[tuple[_dt.date, _dt.date, str]]:
  out = [(_dt.date.fromisoformat(a), _dt.date.fromisoformat(b), r) for a, b, r in _OPCAL_RE.findall(op_cal or "")]
  if not out:
    raise ValueError("no <OperatingCalendar> elements in %r" % (op_cal[:80] if op_cal else op_cal))
  return out


def op_cal_days(op_cal: str) -> set[_dt.date]:
  """Days an OP_CAL string claims. Recurrence '* * * 1-5 *' = Mon-Fri (TfNSW dataset PDF)."""
  days: set[_dt.date] = set()
  for a, b, rec in parse_op_cal(op_cal):
    if rec.split() != ["*", "*", "*", "1-5", "*"]:
      raise ValueError(f"unknown OP_CAL recurrence {rec!r}")
    d = a
    while d <= b:
      if d.weekday() < 5:
        days.add(d)
      d += _dt.timedelta(days=1)
  return days


def audit_op_cal(op_cal: str, division: str, cal: SchoolDayCalendar | None = None) -> dict[str, list[str]]:
  """Compare an OP_CAL string with this calendar over the OP_CAL's own years.

  Returns {'extra': [...], 'missing': [...]} as 'YYYY-MM-DD reason' strings.
  'extra' = OP_CAL says active but it is not a zone day; 'missing' = the reverse.
  """
  cal = cal or get_calendar()
  div = _as_division(division)
  claimed = op_cal_days(op_cal)
  years = {d.year for d in claimed}
  if div is None:
    raise ValueError("audit_op_cal needs a division")
  first, last = cal.coverage[div]
  ours = {d for d in cal.zone_days(div) if d.year in years}
  claimed = {d for d in claimed if first <= d <= last}

  def fmt(d):
    return f"{d.isoformat()} {cal.status(d, div).reason}"

  return {"extra": [fmt(d) for d in sorted(claimed - ours)], "missing": [fmt(d) for d in sorted(ours - claimed)]}


def audit_schoolzones_zip(zip_path: str, cal: SchoolDayCalendar | None = None) -> dict:
  """Audit every record's OP_CAL in TfNSW's schoolzones.zip (stdlib only)."""
  import zipfile

  cal = cal or get_calendar()
  with zipfile.ZipFile(zip_path) as z:
    name = [n for n in z.namelist() if n.lower().endswith(".json")][0]
    data = json.loads(z.read(name))
  head = data[0] if data and isinstance(data[0], dict) and "generated" in data[0] else {}
  recs = [r for r in data if isinstance(r, dict) and "OP_CAL" in r]
  groups: dict[tuple[str, str], int] = {}
  for r in recs:
    key = (str(r.get("LATE_OPENING_SCHOOL")), r.get("OP_CAL") or "")
    groups[key] = groups.get(key, 0) + 1
  out = {"file": os.path.basename(zip_path), "member": name, "generated": head.get("generated"), "records": len(recs), "variants": []}
  for (flag, opc), n in sorted(groups.items(), key=lambda kv: -kv[1]):
    div = division_for_zone(flag)
    v = {"LATE_OPENING_SCHOOL": flag, "records": n, "division": div, "op_cal_ranges": [[a.isoformat(), b.isoformat(), r] for a, b, r in parse_op_cal(opc)]}
    if div:
      v["vs_own_division"] = audit_op_cal(opc, div, cal)
      other = WESTERN if div == EASTERN else EASTERN
      v["vs_other_division"] = audit_op_cal(opc, other, cal)
    out["variants"].append(v)
  return out


# ---------------------------------------------------------------------------
# JSON export / import
# ---------------------------------------------------------------------------


def _ranges(days: Iterable[_dt.date], bridge_weekends: bool = False) -> list[list[str]]:
  """Compress dates into inclusive [first, last] runs of consecutive days.

  bridge_weekends=True also joins runs separated only by Saturdays and
  Sundays, so every Mon-Fri inside a run is in the set.
  """
  out: list[list[_dt.date]] = []
  for d in sorted(days):
    if out:
      gap = [out[-1][1] + _dt.timedelta(days=i) for i in range(1, (d - out[-1][1]).days)]
      if not gap or (bridge_weekends and all(g.weekday() >= 5 for g in gap)):
        out[-1][1] = d
        continue
    out.append([d, d])
  return [[a.isoformat(), b.isoformat()] for a, b in out]


def build_json(schoolzones_zip: str | None = None) -> dict:
  cal, checks, raw = build_calendar(return_checks=True)
  ph, not_ph = raw["ph"], raw["not_ph"]
  cov_last = max(c[1] for c in cal.coverage.values())
  doc = {
    "format": FORMAT,
    "format_version": FORMAT_VERSION,
    "attribution": ATTRIBUTION,
    "generated_utc": _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat(),
    "sources_retrieved": SOURCES_RETRIEVED,
    "rule": (
      "zone day = (term day OR school development day) AND Mon-Fri AND NOT public holiday "
      + "(TfNSW 'School zone days'); equivalently, per Road Rules 2014 r.318(3-1), any weekday that "
      + "is not a public holiday and not a notified government-school holiday. The Bank Holiday "
      + "and local event days are not public holidays, so zones apply on them."
    ),
    "date_semantics": (
      "Local civil date of the zone: Australia/Sydney, or Australia/Broken_Hill in "
      + "the Broken Hill area (30 min behind; same date in any school-zone window). "
      + "SchoolZones.json has 7 zones west of 141.6E and none on Lord Howe Island."
    ),
    "coverage": {k: {"first": a.isoformat(), "last": b.isoformat()} for k, (a, b) in cal.coverage.items()},
    "outside_coverage": "is_school_zone_day() returns None; the caller picks the conservative default.",
    "tfnsw_table_years": sorted(cal.tfnsw_table_years),
    "derived_years": sorted({y for y in DOE_CALENDAR if y not in cal.tfnsw_table_years}),
    "division_selection": {
      "rule": (
        "SchoolZones.json LATE_OPENING_SCHOOL: 'Y' -> western, 'N' -> eastern; missing or "
        + "unknown -> null = union (a zone day if either division says so; identical to eastern "
        + "for 2026-2027 because eastern days are a superset of western days)."
      ),
      "defined_by": (
        "TfNSW dataset PDF: the Late_opening_school Y/N field (Term 1 start differs "
        + "between the Western and Eastern divisions). DoE: the Western division is a named list "
        + "of 'late start' schools in far-west NSW ("
        + URL_DOE_LATE
        + "). TfNSW defines "
        + "no geographic region rule."
      ),
      "evidence": (
        "SchoolZones.json generated 2026-09-07: 129 Y records naming 120 schools. 90 of "
        + "those names match DoE's 117-school late-start list, ignoring 'Public/High/Central "
        + "School'-type suffixes. Of the other 30: 22 are Catholic or independent schools "
        + "(e.g. St Joseph's Nyngan), 3 are TAFE or other sites (Boggabilla TAFE, Warren "
        + "TAFE, Lachlan Park College), 3 appear to be late-start schools under variant "
        + "names (Barwon Learning Centre, Moree Secondary College Albert St, Wakool), and "  # codespell:ignore centre
        + "2 are public schools not on DoE's list (Moama PS, Nevertire PS). Of the 3172 N "
        + "records, 0 name a DoE late-start school."
      ),
      "sydney": "eastern",
    },
    "counts": {k: {str(y): sum(1 for d in cal.zone_days(k) if d.year == y) for y in sorted({d.year for d in cal.zone_days(k)})} for k in DIVISIONS},
    "school_zone_days": {k: [d.isoformat() for d in cal.zone_days(k)] for k in DIVISIONS},
    "school_zone_day_ranges_note": "Inclusive runs: every Mon-Fri inside a run is a zone day; weekends never are.",
    "school_zone_day_ranges": {k: _ranges(cal.zone_days(k), bridge_weekends=True) for k in DIVISIONS},
    "development_days": {k: sorted(d.isoformat() for d in raw["dev"][k] if d.weekday() < 5) for k in DIVISIONS},
    "school_holidays": {k: _ranges(raw["hol"][k]) for k in DIVISIONS},
    "public_holidays": [
      {"date": d.isoformat(), "weekday": _WEEKDAYS[d.weekday()], "name": n, "in_term": {k: d in raw["terms"][k] or d in raw["dev"][k] for k in DIVISIONS}}
      for d, n in sorted(ph.items())
      if COVERAGE_FIRST <= d <= cov_last
    ],
    "not_public_holidays": [
      {
        "date": d.isoformat(),
        "name": n,
        "zone_day": True,
        "why": "nsw.gov.au table note: 'The Bank Holiday is not a declared public holiday'; schools are open.",
      }
      for d, n in sorted(not_ph.items())
    ],
    "checks": checks,
    "source_strings": {
      "tfnsw_table": {str(y): v for y, v in TFNSW_TABLE.items()},
      "doe_calendar": {str(y): {k: v[k] for k in DIVISIONS} for y, v in DOE_CALENDAR.items()},
      "doe_future_2027": DOE_FUTURE_2027,
      "public_holidays": [list(x) for x in PUBLIC_HOLIDAYS],
      "not_public_holidays": [list(x) for x in NOT_PUBLIC_HOLIDAYS],
    },
    "sources": SOURCES,
    "non_standard_zones": [
      "30 km/h school zones (7 School lines at 30 in the speed-zone layer; TfNSW cites those inside the Manly 30 km/h HPAA): "
      + "same days; only the speed differs.",
      "Non-standard times (red/orange signs): times come from SchoolZones.json START/END_TIME_*; the days are these.",
      (
        "Border schools: DoE says a few schools near the VIC/QLD borders run different dates. The data does not mark "
        + "them; r.318(3-1) keys zones to notified government-school holidays, so the division calendar is used "
        + "(e.g. Moama PS is flagged Y although it is not on DoE's late-start list)."
      ),
      "Zone types 'School Bus' (4 lines) and 'Wet Weather' (3) are NOT school-day driven; do not apply this calendar to them.",
      "School lines with no SchoolZones.json polygon: division unknown -> pass None (union).",
    ],
    "known_limitations": [
      (
        "2027 is derived from DoE's 2027 page using TfNSW's own rule; TfNSW's page listed only 2026 when retrieved "
        + "(its dataset PDF says new term dates are populated during the Term 4 holidays). strict=True treats 2027 weekdays as unknown."
      ),
      (
        "Part-State public holidays (Public Holidays Act s.5) can be declared at 7 days' notice; none are listed for 2026-2027. "
        + "A newly declared holiday needs a data refresh."
      ),
      "Unplanned closures (fire, flood, emergency) are not modeled; the signs' flashing lights still run on those days.",
      "Public holidays in Jan 2028 are not published yet; every weekday up to the coverage end is a school holiday anyway.",
      "DoE's 2028-2030 term ranges exist but are not used until nsw.gov.au publishes those years' public holidays.",
    ],
    "update_procedure": (
      "Paste the new verbatim strings into TFNSW_TABLE / DOE_CALENDAR / PUBLIC_HOLIDAYS in "
      + "openpilot/sunnypilot/mapd/nsw_zones/school_days.py, run its tests "
      + "(tests/test_school_days.py) and rebuild the index. The checks refuse weekday-name typos, gaps, overlaps, "
      + "and any TfNSW/DoE or rule/Road-Rules disagreement."
    ),
  }
  if schoolzones_zip and os.path.exists(schoolzones_zip):
    doc["op_cal_audit"] = audit_schoolzones_zip(schoolzones_zip, cal)
    doc["op_cal_audit"]["finding"] = _op_cal_finding(doc["op_cal_audit"], raw["term_starts"])
  return doc


def _op_cal_finding(audit: dict, term_starts) -> list[str]:
  notes = []
  for v in audit["variants"]:
    div = v["division"]
    if not div:
      continue
    t1 = v["op_cal_ranges"][0][0]
    y = int(t1[:4])
    own = term_starts[div].get(y, [None])[0]
    other = term_starts[WESTERN if div == EASTERN else EASTERN].get(y, [None])[0]
    s = f"{v['records']} records flagged {v['LATE_OPENING_SCHOOL']} ({div}): OP_CAL Term 1 starts {t1}; {div} students start {own}"
    if own and t1 != own.isoformat() and other and t1 == other.isoformat():
      s += " -> SWAPPED (it is the other division's start)"
    notes.append(s)
  notes.append("OP_CAL omits the development days, although TfNSW says zones operate on them.")
  notes.append("OP_CAL does not exclude public holidays that fall inside a term (2026: 27 Apr Anzac additional day, 8 Jun King's Birthday).")
  notes.append("OP_CAL covers the current year only.")
  return notes


def calendar_from_json(doc: dict) -> SchoolDayCalendar:
  if doc.get("format") != FORMAT or int(doc.get("format_version", -1)) != FORMAT_VERSION:
    raise ValueError(f"not a {FORMAT} v{FORMAT_VERSION} document")
  iso = _dt.date.fromisoformat
  cov = {k: (iso(doc["coverage"][k]["first"]), iso(doc["coverage"][k]["last"])) for k in DIVISIONS}
  zone = {k: [iso(s) for s in doc["school_zone_days"][k]] for k in DIVISIONS}
  dev = {k: [iso(s) for s in doc["development_days"][k]] for k in DIVISIONS}
  ph = {iso(p["date"]): p["name"] for p in doc["public_holidays"]}
  for k in DIVISIONS:
    a, b = cov[k]
    if not zone[k] or min(zone[k]) < a or max(zone[k]) > b:
      raise ValueError(f"zone days outside coverage for {k}")
  return SchoolDayCalendar(
    zone,
    dev,
    ph,
    cov,
    doc.get("tfnsw_table_years", []),
    meta={"source": "json", "generated_utc": doc.get("generated_utc"), "sources_retrieved": doc.get("sources_retrieved")},
  )


def load_calendar(path: str) -> SchoolDayCalendar:
  with open(path, encoding="utf-8") as f:
    return calendar_from_json(json.load(f))


def write_json(path: str, schoolzones_zip: str | None = None) -> dict:
  doc = build_json(schoolzones_zip)
  os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
  tmp = path + ".tmp"
  with open(tmp, "w", encoding="utf-8", newline="\n") as f:
    json.dump(doc, f, indent=1, ensure_ascii=False)
    f.write("\n")
  os.replace(tmp, path)
  # Round trip: the exported file must answer exactly like the embedded build.
  a, b = build_calendar(), load_calendar(path)
  for k in (EASTERN, WESTERN, None):
    d = COVERAGE_FIRST - _dt.timedelta(days=10)
    while d <= max(c[1] for c in a.coverage.values()) + _dt.timedelta(days=10):
      if a.status(d, k) != b.status(d, k):
        raise ValueError(f"JSON round trip differs on {d} {k}")
      d += _dt.timedelta(days=1)
  return doc


def _main(argv: list[str]) -> int:
  import argparse

  ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
  ap.add_argument("--out", default="nsw_school_days.json")
  ap.add_argument("--schoolzones", default=None, help="TfNSW schoolzones.zip for the OP_CAL audit (optional)")
  ap.add_argument("--check", nargs="*", metavar="YYYY-MM-DD", help="print the status of these dates and exit")
  ap.add_argument("--division", default=None, choices=[EASTERN, WESTERN])
  a = ap.parse_args(argv)
  if a.check is not None:
    for s in a.check:
      st = day_status(s, a.division)
      derived = "" if st.tfnsw_table else "  [derived: not in TfNSW table]"
      print(f"{s} {_WEEKDAYS[_as_date(s).weekday()]:<9} {st.zone_day!s:<6} {st.reason}{derived}")
    return 0
  doc = write_json(a.out, a.schoolzones)
  print(f"wrote {a.out}")
  for k in DIVISIONS:
    print(f"  {k:<8} coverage {doc['coverage'][k]['first']} .. {doc['coverage'][k]['last']}, zone days per year {doc['counts'][k]}")
  for n in doc.get("op_cal_audit", {}).get("finding", []):
    print("  OP_CAL: " + n)
  return 0


if __name__ == "__main__":
  sys.exit(_main(sys.argv[1:]))
