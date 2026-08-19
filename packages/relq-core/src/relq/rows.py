"""Explicit, validated conversion from database tuples to Python row models."""

from __future__ import annotations

import datetime
import decimal
import enum
import ipaddress
import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields, is_dataclass
from typing import NewType, Protocol, cast, get_type_hints

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type Inet = (
    ipaddress.IPv4Address
    | ipaddress.IPv6Address
    | ipaddress.IPv4Interface
    | ipaddress.IPv6Interface
)

NaiveDateTime = NewType("NaiveDateTime", datetime.datetime)
AwareDateTime = NewType("AwareDateTime", datetime.datetime)
NaiveTime = NewType("NaiveTime", datetime.time)
AwareTime = NewType("AwareTime", datetime.time)


def naive_datetime(value: datetime.datetime) -> NaiveDateTime:
    if value.tzinfo is not None:
        raise ValueError("naive datetime must not have tzinfo")
    return NaiveDateTime(value)


def aware_datetime(value: datetime.datetime) -> AwareDateTime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware datetime must have a concrete UTC offset")
    return AwareDateTime(value)


def naive_time(value: datetime.time) -> NaiveTime:
    if value.tzinfo is not None:
        raise ValueError("naive time must not have tzinfo")
    return NaiveTime(value)


def aware_time(value: datetime.time) -> AwareTime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware time must have a concrete UTC offset")
    return AwareTime(value)


@dataclass(frozen=True, slots=True)
class Interval:
    """PostgreSQL's lossless ``(months, days, microseconds)`` interval value."""

    months: int = 0
    days: int = 0
    microseconds: int = 0

    def __post_init__(self) -> None:
        if any(type(value) is not int for value in (self.months, self.days, self.microseconds)):
            raise TypeError("interval components must be integers")


class RowDecodingError(ValueError):
    """A result value could not be decoded for a declared row model."""


@dataclass(frozen=True, slots=True)
class Decoder[Value]:
    """A named, explicit conversion accepted by :class:`RowAdapter`."""

    name: str
    _decode: Callable[[object], Value]

    def decode(self, value: object) -> Value:
        return self._decode(value)


class _AnyDecoder(Protocol):
    @property
    def name(self) -> str: ...

    def decode(self, value: object) -> object: ...


@dataclass(frozen=True, slots=True)
class _Field:
    name: str
    decoder: _AnyDecoder | None


@dataclass(frozen=True, slots=True)
class RowAdapter[Model]:
    """A checked positional-row adapter for a dataclass or ``NamedTuple``.

    Drivers return raw tuples by default. An adapter is the explicit trust
    boundary: it validates result arity, optionally decodes each field, then
    calls the model constructor. It never infers conversions from annotations.
    """

    _model: type[Model]
    _factory: Callable[..., Model]
    _fields: tuple[_Field, ...]

    def map(self, row: Sequence[object]) -> Model:
        if len(row) != self.arity:
            raise ValueError(
                f"{self._model.__name__} requires {self.arity} result columns; "
                f"query returned {len(row)}"
            )
        values: list[object] = []
        for index, (field, value) in enumerate(zip(self._fields, row, strict=True)):
            if field.decoder is None:
                values.append(value)
                continue
            try:
                values.append(field.decoder.decode(value))
            except Exception as error:
                raise RowDecodingError(
                    f"cannot decode {self._model.__name__}.{field.name} at result column "
                    f"{index}: expected {field.decoder.name}, received "
                    f"{value!r} ({type(value).__name__})"
                ) from error
        return self._factory(*values)

    @property
    def arity(self) -> int:
        return len(self._fields)

    @property
    def model_name(self) -> str:
        return self._model.__name__

    def is_compatible_with[Other](self, other: RowAdapter[Other]) -> bool:
        """Whether two query arms have one deterministic result mapping."""
        return self._model is other._model and tuple(
            (field.name, None if field.decoder is None else field.decoder.name)
            for field in self._fields
        ) == tuple(
            (field.name, None if field.decoder is None else field.decoder.name)
            for field in other._fields
        )


def row_adapter[Model](
    model: type[Model], *, decoders: Sequence[_AnyDecoder | None] | None = None
) -> RowAdapter[Model]:
    """Create a strict adapter for a dataclass or ``typing.NamedTuple`` class."""
    names = _field_names(model)
    if decoders is None:
        resolved = (None,) * len(names)
    else:
        resolved = tuple(decoders)
        if len(resolved) != len(names):
            raise ValueError(
                f"{model.__name__} has {len(names)} fields but received {len(resolved)} decoders"
            )
    fields_ = tuple(_Field(name, decoder) for name, decoder in zip(names, resolved, strict=True))
    return RowAdapter(model, model, fields_)


def nullable[Value](decoder: Decoder[Value]) -> Decoder[Value | None]:
    """Allow SQL ``NULL`` before applying an explicit field decoder."""
    return Decoder(
        f"nullable {decoder.name}", lambda value: None if value is None else decoder.decode(value)
    )


def enum_decoder[Value: enum.Enum](enum_type: type[Value]) -> Decoder[Value]:
    return Decoder(enum_type.__name__, enum_type)


def uuid_decoder() -> Decoder[uuid.UUID]:
    def decode(value: object) -> uuid.UUID:
        if isinstance(value, uuid.UUID):
            return value
        if isinstance(value, str):
            return uuid.UUID(value)
        raise TypeError("UUID values must be UUID instances or strings")

    return Decoder("UUID", decode)


def inet_decoder() -> Decoder[Inet]:
    """Decode asyncpg's exact ``inet`` result values at the row trust boundary."""

    inet_types = (
        ipaddress.IPv4Address,
        ipaddress.IPv6Address,
        ipaddress.IPv4Interface,
        ipaddress.IPv6Interface,
    )

    def decode(value: object) -> Inet:
        if isinstance(value, inet_types):
            return value
        if isinstance(value, str):
            return ipaddress.ip_interface(value) if "/" in value else ipaddress.ip_address(value)
        raise TypeError("inet values must be IP addresses, interfaces, or text")

    return Decoder("inet", decode)


def decimal_decoder() -> Decoder[decimal.Decimal]:
    def decode(value: object) -> decimal.Decimal:
        if isinstance(value, decimal.Decimal):
            return value
        if isinstance(value, (int, str)) and not isinstance(value, bool):
            return decimal.Decimal(value)
        if isinstance(value, float):
            return decimal.Decimal(str(value))
        raise TypeError("Decimal values must be Decimal, int, float, or str")

    return Decoder("Decimal", decode)


def datetime_decoder() -> Decoder[datetime.datetime]:
    return _temporal_decoder("datetime", datetime.datetime, datetime.datetime.fromisoformat)


def naive_datetime_decoder() -> Decoder[NaiveDateTime]:
    return Decoder("naive datetime", lambda value: naive_datetime(datetime_decoder().decode(value)))


def aware_datetime_decoder() -> Decoder[AwareDateTime]:
    return Decoder("aware datetime", lambda value: aware_datetime(datetime_decoder().decode(value)))


def date_decoder() -> Decoder[datetime.date]:
    return _temporal_decoder("date", datetime.date, datetime.date.fromisoformat)


def time_decoder() -> Decoder[datetime.time]:
    return _temporal_decoder("time", datetime.time, datetime.time.fromisoformat)


def naive_time_decoder() -> Decoder[NaiveTime]:
    return Decoder("naive time", lambda value: naive_time(time_decoder().decode(value)))


def aware_time_decoder() -> Decoder[AwareTime]:
    return Decoder("aware time", lambda value: aware_time(time_decoder().decode(value)))


def interval_decoder() -> Decoder[Interval]:
    def decode(value: object) -> Interval:
        if isinstance(value, Interval):
            return value
        raise TypeError("interval values must be relq.Interval instances")

    return Decoder("Interval", decode)


def json_decoder() -> Decoder[JsonValue]:
    def decode(value: object) -> JsonValue:
        if isinstance(value, (str, bytes, bytearray)):
            return _json_value(cast(object, json.loads(value)))
        # Drivers that decode JSON return arbitrary Python values. Re-encoding
        # validates that shape before applying the same typed parse path.
        return _json_value(cast(object, json.loads(json.dumps(value))))

    return Decoder("JSON", decode)


def int_decoder() -> Decoder[int]:
    return _instance_decoder("int", int, reject_bool=True)


def float_decoder() -> Decoder[float]:
    return _instance_decoder("float", float)


def str_decoder() -> Decoder[str]:
    return _instance_decoder("str", str)


def bool_decoder() -> Decoder[bool]:
    return _instance_decoder("bool", bool)


def bytes_decoder() -> Decoder[bytes]:
    return _instance_decoder("bytes", bytes)


def list_decoder[Value](decoder: Decoder[Value]) -> Decoder[list[Value]]:
    def decode(value: object) -> list[Value]:
        if not isinstance(value, list):
            raise TypeError("array values must be lists")
        return [decoder.decode(item) for item in _list_items(cast(list[object], value))]

    return Decoder(f"list[{decoder.name}]", decode)


def domain_decoder[Base, Domain](
    name: str, base: Decoder[Base], wrap: Callable[[Base], Domain]
) -> Decoder[Domain]:
    """Decode a domain's base value, then apply its deliberate wrapper."""
    return Decoder(name, lambda value: wrap(base.decode(value)))


def _field_names[Model](model: type[Model]) -> tuple[str, ...]:
    if is_dataclass(model):
        return tuple(field.name for field in fields(model) if field.init)
    if "_fields" not in vars(model):
        raise TypeError("row_adapter requires a dataclass or NamedTuple class")
    annotations = get_type_hints(model)
    if not annotations:
        raise TypeError("row_adapter requires a dataclass or NamedTuple class")
    return tuple(annotations)


def _temporal_decoder[Value](
    name: str, expected: type[Value], parse: Callable[[str], Value]
) -> Decoder[Value]:
    def decode(value: object) -> Value:
        if isinstance(value, expected):
            return value
        if isinstance(value, str):
            return parse(value)
        raise TypeError(f"{name} values must be {name} instances or ISO strings")

    return Decoder(name, decode)


def _instance_decoder[Value](
    name: str, expected: type[Value], *, reject_bool: bool = False
) -> Decoder[Value]:
    def decode(value: object) -> Value:
        if isinstance(value, expected) and not (reject_bool and isinstance(value, bool)):
            return value
        raise TypeError(f"expected {name}")

    return Decoder(name, decode)


def _json_value(value: object) -> JsonValue:
    """The stdlib JSON parser guarantees this recursive value shape."""
    return cast(JsonValue, value)


def _list_items(value: list[object]) -> tuple[object, ...]:
    """Prevent a driver's ``list[Unknown]`` from leaking into decoders."""
    return tuple(value)
