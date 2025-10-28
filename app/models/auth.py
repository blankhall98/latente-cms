from __future__ import annotations
from datetime import datetime
from enum import Enum
from sqlalchemy import (
    String, Integer, Boolean, DateTime, ForeignKey, Enum as SQLEnum,
    UniqueConstraint, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    users: Mapped[list[UserTenant]] = relationship("UserTenant", back_populates="tenant", cascade="all, delete-orphan")
    api_keys: Mapped[list[ApiKey]] = relationship("ApiKey", back_populates="tenant", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(160), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenants: Mapped[list[UserTenant]] = relationship("UserTenant", back_populates="user", cascade="all, delete-orphan")


class RoleScope(str, Enum):
    core = "core"
    owa = "owa"
    anro = "anro"
    custom = "custom"


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(160))
    scope: Mapped[RoleScope] = mapped_column(SQLEnum(RoleScope, native_enum=False), default=RoleScope.core)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)

    permissions: Mapped[list[RolePermission]] = relationship("RolePermission", back_populates="role", cascade="all, delete-orphan")


class PermissionScope(str, Enum):
    core = "core"
    owa = "owa"
    anro = "anro"
    custom = "custom"


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255), default=None)
    scope: Mapped[PermissionScope] = mapped_column(SQLEnum(PermissionScope, native_enum=False), default=PermissionScope.core)

    roles: Mapped[list[RolePermission]] = relationship("RolePermission", back_populates="permission", cascade="all, delete-orphan")


class UserTenantStatus(str, Enum):
    active = "active"
    pending = "pending"
    removed = "removed"


class UserTenant(Base):
    __tablename__ = "user_tenants"
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant"),
        Index("ix_user_tenant_tenant_id", "tenant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    status: Mapped[UserTenantStatus] = mapped_column(SQLEnum(UserTenantStatus, native_enum=False), default=UserTenantStatus.active)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship("User", back_populates="tenants")
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="users")
    role: Mapped[Role] = relationship("Role")


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),
        Index("ix_role_permission_role_id", "role_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"))
    permission_id: Mapped[int] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"))

    role: Mapped[Role] = relationship("Role", back_populates="permissions")
    permission: Mapped[Permission] = relationship("Permission", back_populates="roles")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(160))
    key_hash: Mapped[str] = mapped_column(String(128), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="api_keys")
