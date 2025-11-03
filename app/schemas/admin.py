# app/schemas/admin.py
from __future__ import annotations
from typing import Optional, Literal
from pydantic import BaseModel, EmailStr

# ===== Users =====
class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    is_active: bool = True
    is_superadmin: bool = False

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None
    is_superadmin: Optional[bool] = None  # solo superadmins pueden cambiar esto

class UserOut(BaseModel):
    id: int
    email: EmailStr
    full_name: Optional[str] = None
    is_active: bool
    is_superadmin: bool
    class Config:
        from_attributes = True

# ===== Tenants =====
class TenantCreate(BaseModel):
    name: str
    slug: str

class TenantUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None

class TenantOut(BaseModel):
    id: int
    name: str
    slug: str
    class Config:
        from_attributes = True

# ===== Members (UserTenant) =====
class MemberCreate(BaseModel):
    user_id: int
    tenant_id: int
    role_id: int  # asignación inicial
    status: Optional[Literal["active", "invited", "suspended"]] = "active"

class MemberUpdate(BaseModel):
    role_id: Optional[int] = None
    status: Optional[Literal["active", "invited", "suspended"]] = None

class MemberOut(BaseModel):
    id: int
    user_id: int
    tenant_id: int
    role_id: int
    status: str
    class Config:
        from_attributes = True

# ===== Roles / Permissions (lectura) =====
class RoleOut(BaseModel):
    id: int
    key: str
    label: str
    scope: Optional[str] = None
    is_system: bool
    class Config:
        from_attributes = True

class PermissionOut(BaseModel):
    id: int
    key: str
    description: Optional[str] = None
    scope: Optional[str] = None
    class Config:
        from_attributes = True
