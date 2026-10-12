"""Compiler value objects shared by the compilation façade and renderer."""

from dataclasses import dataclass

from relq._frozen import FrozenSlots


@dataclass(frozen=True, init=False)
class Dialect(FrozenSlots):
    """The limited dialect variation supported by relq's renderer."""

    __slots__ = (
        "max_parameters",
        "name",
        "placeholder",
        "supports_conflict_predicates",
        "supports_data_modifying_ctes",
        "supports_postgres_expressions",
        "supports_returning",
        "supports_row_locking",
        "supports_schema_qualified_tables",
        "supports_temporal_arithmetic",
    )

    name: str
    placeholder: str
    supports_returning: bool
    supports_row_locking: bool
    supports_temporal_arithmetic: bool
    supports_conflict_predicates: bool
    max_parameters: int | None
    supports_schema_qualified_tables: bool
    supports_data_modifying_ctes: bool
    supports_postgres_expressions: bool

    def __init__(
        self,
        name: str,
        placeholder: str,
        supports_returning: bool = True,
        supports_row_locking: bool = False,
        supports_temporal_arithmetic: bool = False,
        supports_conflict_predicates: bool = False,
        max_parameters: int | None = None,
        supports_schema_qualified_tables: bool = False,
        supports_data_modifying_ctes: bool = False,
        supports_postgres_expressions: bool = False,
    ) -> None:
        # Slots rule out class-level field defaults, so the defaults live here.
        set_field = object.__setattr__
        set_field(self, "name", name)
        set_field(self, "placeholder", placeholder)
        set_field(self, "supports_returning", supports_returning)
        set_field(self, "supports_row_locking", supports_row_locking)
        set_field(self, "supports_temporal_arithmetic", supports_temporal_arithmetic)
        set_field(self, "supports_conflict_predicates", supports_conflict_predicates)
        set_field(self, "max_parameters", max_parameters)
        set_field(self, "supports_schema_qualified_tables", supports_schema_qualified_tables)
        set_field(self, "supports_data_modifying_ctes", supports_data_modifying_ctes)
        set_field(self, "supports_postgres_expressions", supports_postgres_expressions)


@dataclass(frozen=True)
class CompiledQuery(FrozenSlots):
    """Database-ready SQL plus its separately bound parameter values."""

    __slots__ = ("parameters", "sql")

    sql: str
    parameters: tuple[object, ...]
