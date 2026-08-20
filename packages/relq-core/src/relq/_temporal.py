"""Closed PostgreSQL temporal vocabulary shared by builders and validation.

This module is deliberately a leaf: public expression builders re-export its
enums while the private compiler uses the same values to defend handcrafted
ASTs. Keeping the vocabulary here prevents the public surface and compiler
from accepting different temporal tokens.
"""

import enum


class ExtractField(enum.StrEnum):
    """The PostgreSQL fields accepted by :func:`relq.extract`."""

    CENTURY = "century"
    DAY = "day"
    DECADE = "decade"
    DOW = "dow"
    DOY = "doy"
    EPOCH = "epoch"
    HOUR = "hour"
    ISODOW = "isodow"
    ISOYEAR = "isoyear"
    JULIAN = "julian"
    MICROSECONDS = "microseconds"
    MILLENNIUM = "millennium"
    MILLISECONDS = "milliseconds"
    MINUTE = "minute"
    MONTH = "month"
    QUARTER = "quarter"
    SECOND = "second"
    TIMEZONE = "timezone"
    TIMEZONE_HOUR = "timezone_hour"
    TIMEZONE_MINUTE = "timezone_minute"
    WEEK = "week"
    YEAR = "year"


class TruncUnit(enum.StrEnum):
    """The PostgreSQL units accepted by :func:`relq.date_trunc`."""

    MICROSECOND = "microseconds"
    MILLISECOND = "milliseconds"
    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"
    DECADE = "decade"
    CENTURY = "century"
    MILLENNIUM = "millennium"


EXTRACT_FIELD_VALUES = frozenset(field.value for field in ExtractField)
TRUNC_UNIT_VALUES = frozenset(unit.value for unit in TruncUnit)
