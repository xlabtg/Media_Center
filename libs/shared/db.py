from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, TypeVar, cast

from sqlalchemy import (
    CHAR,
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from libs.shared.tenant import (
    AuditSink,
    TenantContext,
    TenantScopedRepository,
    require_tenant_context,
)

DATABASE_URL_ENV = "DATABASE_URL"
POSTGRESQL_ASYNCPG_DRIVER = "postgresql+asyncpg"
NAMING_CONVENTION: dict[str, str] = {
    "ix": "idx_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

ModelT = TypeVar("ModelT")
ModelT_co = TypeVar("ModelT_co", covariant=True)


class ScalarRows[ModelT_co](Protocol):
    def all(self) -> Sequence[ModelT_co]:
        """Return all rows from a SQLAlchemy scalar result."""


class AsyncScalarSession[ModelT](Protocol):
    async def scalars(
        self,
        statement: Select[tuple[ModelT]],
    ) -> ScalarRows[ModelT]:
        """Execute a typed scalar select statement."""


class AddSession(Protocol):
    def add(self, instance: object) -> None:
        """Stage an ORM instance for persistence."""


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TenantOwnedMixin:
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_tenants_slug"),
        Index("idx_tenants_status", "status"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
        server_default=text("'active'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TenantSetting(Base, TenantOwnedMixin):
    __tablename__ = "tenant_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_tenant_settings_tenant_key"),
        Index("idx_tenant_settings_tenant_updated", "tenant_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tenants.tenant_id", name="fk_tenant_settings_tenant_id_tenants"),
        nullable=False,
        index=True,
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value_json: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Contribution(Base, TenantOwnedMixin):
    __tablename__ = "contributions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_contributions_tenant_idempotency",
        ),
        CheckConstraint(
            "points_awarded >= 0",
            name="points_non_negative",
        ),
        Index(
            "idx_contributions_tenant_event_created",
            "tenant_id",
            "event_type",
            "created_at",
        ),
        Index(
            "idx_contributions_tenant_member_occurred",
            "tenant_id",
            "member_id",
            "occurred_at",
        ),
        Index(
            "idx_contributions_tenant_source",
            "tenant_id",
            "source_type",
            "source_ref",
        ),
        Index("idx_contributions_audit_hash", "audit_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tenants.tenant_id", name="fk_contributions_tenant_id_tenants"),
        nullable=False,
        index=True,
    )
    member_id: Mapped[str] = mapped_column(String(36), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    points_awarded: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
    )
    audit_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class TenantWeight(Base, TenantOwnedMixin):
    __tablename__ = "tenant_weights"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "member_id",
            "period",
            name="uq_tenant_weights_tenant_member_period",
        ),
        CheckConstraint(
            "total_points >= 0 AND avg_points_council >= 0 AND "
            "kv_raw >= 0 AND kv_capped >= 0 AND payout_share >= 0",
            name="values_non_negative",
        ),
        CheckConstraint("kv_capped <= 0.10", name="kv_cap"),
        CheckConstraint("payout_share <= 1", name="payout_share"),
        Index("idx_tenant_weights_tenant_period", "tenant_id", "period"),
        Index(
            "idx_tenant_weights_tenant_period_kv",
            "tenant_id",
            "period",
            "kv_capped",
        ),
        Index("idx_tenant_weights_calculation_hash", "calculation_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tenants.tenant_id", name="fk_tenant_weights_tenant_id_tenants"),
        nullable=False,
        index=True,
    )
    member_id: Mapped[str] = mapped_column(String(36), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    total_points: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    avg_points_council: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )
    kv_raw: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    kv_capped: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    payout_share: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    calculation_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    database_url: str
    echo: bool = False
    pool_pre_ping: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "database_url",
            validate_database_url(self.database_url),
        )

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        env_var: str = DATABASE_URL_ENV,
        echo: bool = False,
        pool_pre_ping: bool = True,
    ) -> DatabaseSettings:
        return cls(
            database_url=database_url_from_env(environ, env_var=env_var),
            echo=echo,
            pool_pre_ping=pool_pre_ping,
        )


@dataclass(frozen=True, slots=True)
class AsyncDatabase:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    @classmethod
    def from_settings(cls, settings: DatabaseSettings) -> AsyncDatabase:
        engine = create_async_engine_from_url(
            settings.database_url,
            echo=settings.echo,
            pool_pre_ping=settings.pool_pre_ping,
        )
        return cls(
            engine=engine,
            session_factory=create_async_session_factory(engine),
        )

    @classmethod
    def from_url(
        cls,
        database_url: str | None = None,
        *,
        echo: bool = False,
        pool_pre_ping: bool = True,
    ) -> AsyncDatabase:
        settings = DatabaseSettings(
            database_url=(
                database_url if database_url is not None else database_url_from_env()
            ),
            echo=echo,
            pool_pre_ping=pool_pre_ping,
        )
        return cls.from_settings(settings)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            else:
                await session.commit()

    async def dispose(self) -> None:
        await self.engine.dispose()


class TenantScopedSQLAlchemyRepository[ModelT]:
    """Tenant-aware base repository for SQLAlchemy async ORM models."""

    def __init__(
        self,
        model: type[ModelT],
        *,
        resource_type: str,
        id_field: str = "id",
    ) -> None:
        if resource_type.strip() == "":
            raise ValueError("resource_type должен быть непустой строкой")
        if id_field.strip() == "":
            raise ValueError("id_field должен быть непустой строкой")

        self.model = model
        self.resource_type = resource_type
        self.id_field = id_field
        self._tenant_guard = TenantScopedRepository[ModelT](resource_type)
        self._tenant_column()
        self._id_column()

    def tenant_filter(
        self,
        context: TenantContext | None = None,
    ) -> ColumnElement[bool]:
        resolved_context = _resolve_context(context)
        return self._tenant_column() == resolved_context.tenant_id

    def statement_for_tenant(
        self,
        context: TenantContext | None = None,
    ) -> Select[tuple[ModelT]]:
        return select(self.model).where(self.tenant_filter(context))

    async def list_for_tenant(
        self,
        session: AsyncScalarSession[ModelT],
        context: TenantContext | None = None,
    ) -> list[ModelT]:
        result = await session.scalars(self.statement_for_tenant(context))
        return list(result.all())

    async def get_owned(
        self,
        session: AsyncScalarSession[ModelT],
        resource_id: object,
        context: TenantContext | None = None,
        *,
        audit_sink: AuditSink | None = None,
    ) -> ModelT | None:
        resolved_context = _resolve_context(context)
        statement = select(self.model).where(self._id_column() == resource_id).limit(1)
        result = await session.scalars(statement)
        records = list(result.all())
        if not records:
            return None

        return self.require_owned(
            records[0],
            resolved_context,
            audit_sink=audit_sink,
        )

    def add(
        self,
        session: AddSession,
        record: ModelT,
        context: TenantContext | None = None,
        *,
        audit_sink: AuditSink | None = None,
    ) -> ModelT:
        owned_record = self.require_owned(record, context, audit_sink=audit_sink)
        session.add(owned_record)
        return owned_record

    def require_owned(
        self,
        record: ModelT,
        context: TenantContext | None = None,
        *,
        audit_sink: AuditSink | None = None,
    ) -> ModelT:
        return self._tenant_guard.require_owned(
            record,
            context,
            audit_sink=audit_sink,
        )

    def _tenant_column(self) -> InstrumentedAttribute[str]:
        column = getattr(self.model, "tenant_id", None)
        if column is None:
            raise TypeError(
                f"{self.model.__name__} должен содержать tenant_id для "
                "tenant-aware repository",
            )

        return cast(InstrumentedAttribute[str], column)

    def _id_column(self) -> InstrumentedAttribute[object]:
        column = getattr(self.model, self.id_field, None)
        if column is None:
            raise TypeError(
                f"{self.model.__name__} должен содержать поле {self.id_field}",
            )

        return cast(InstrumentedAttribute[object], column)


def _resolve_context(context: TenantContext | None) -> TenantContext:
    if context is not None:
        return context

    return require_tenant_context()


def database_url_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    env_var: str = DATABASE_URL_ENV,
) -> str:
    source = os.environ if environ is None else environ
    database_url = source.get(env_var)
    if database_url is None or database_url.strip() == "":
        raise ValueError(f"{env_var} должен быть задан")

    return validate_database_url(database_url)


def validate_database_url(database_url: str) -> str:
    normalized_url = database_url.strip()
    if normalized_url == "":
        raise ValueError("DATABASE_URL должен быть непустой строкой")

    url = make_url(normalized_url)
    if url.drivername != POSTGRESQL_ASYNCPG_DRIVER:
        raise ValueError(
            f"DATABASE_URL должен использовать driver {POSTGRESQL_ASYNCPG_DRIVER}",
        )

    return normalized_url


def create_async_engine_from_url(
    database_url: str | None = None,
    *,
    echo: bool = False,
    pool_pre_ping: bool = True,
) -> AsyncEngine:
    resolved_url = database_url_from_env() if database_url is None else database_url
    return create_async_engine(
        validate_database_url(resolved_url),
        echo=echo,
        pool_pre_ping=pool_pre_ping,
    )


def create_async_session_factory(
    engine: AsyncEngine,
    *,
    expire_on_commit: bool = False,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        expire_on_commit=expire_on_commit,
        class_=AsyncSession,
    )
