import datetime

from relq import (
    AwareDateTime,
    AwareTime,
    Column,
    Interval,
    NaiveDateTime,
    NaiveTime,
    Table,
    TruncUnit,
    age,
    at_time_zone,
    column,
    date_bin,
    date_difference,
    date_trunc,
    extract,
    overlaps,
    time_difference,
    timestamp_difference,
)


class Temporal(Table):
    date_value: Column[datetime.date] = column(datetime.date)
    naive_value: Column[NaiveDateTime] = column(NaiveDateTime)
    aware_value: Column[AwareDateTime] = column(AwareDateTime)
    naive_time: Column[NaiveTime] = column(NaiveTime)
    aware_time: Column[AwareTime] = column(AwareTime)
    interval_value: Column[Interval] = column(Interval)


temporal = Temporal("temporal")

# Temporal arithmetic and calendar functions reject mixed PostgreSQL domains.
age(temporal.naive_value, temporal.aware_value)
date_difference(temporal.date_value, temporal.naive_value)
time_difference(temporal.naive_time, temporal.aware_time)
timestamp_difference(temporal.naive_value, temporal.aware_value)
at_time_zone(temporal.date_value, "UTC")
date_trunc(TruncUnit.DAY, temporal.naive_value, "UTC")
date_bin(Interval(days=1), temporal.naive_value, temporal.aware_value)
overlaps(
    temporal.naive_value,
    temporal.naive_value,
    temporal.aware_value,
    temporal.aware_value,
)

# Compiler-owned temporal domains do not accept arbitrary strings or values.
extract("year", temporal.date_value)
