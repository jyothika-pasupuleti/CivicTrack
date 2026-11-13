from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class IssueLocation(BaseModel):
    address: str | None = Field(default=None, max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @field_validator("address", mode="before")
    @classmethod
    def normalize_address(cls, value: str | None) -> str | None:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def validate_coordinates(self) -> "IssueLocation":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Provide both latitude and longitude, or neither.")
        if self.address is None and self.latitude is None:
            raise ValueError(
                "Please provide an address or select a location on the map to submit your complaint."
            )
        return self


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=10, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    category_ids: list[int] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_coordinates(self) -> "DepartmentCreate":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Provide both latitude and longitude, or neither.")
        if any(category_id < 1 for category_id in self.category_ids):
            raise ValueError("Category IDs must be positive.")
        return self


class DepartmentRename(BaseModel):
    name: str = Field(min_length=2, max_length=120)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class IssueUpdate(BaseModel):
    status: str | None = Field(default=None, max_length=24)
    assigned_department_id: int | None = Field(default=None, ge=1)
    category_id: int | None = Field(default=None, ge=1)
    severity: int | None = Field(default=None, ge=1, le=5)
    note: str | None = Field(default=None, max_length=500)


class SlaPolicyUpsert(BaseModel):
    category_id: int = Field(ge=1)
    severity_min: int = Field(ge=1, le=5)
    response_minutes: int = Field(ge=1, le=525600)
    resolution_minutes: int = Field(ge=1, le=525600)


class CategorySuggestionRequest(BaseModel):
    title: str = Field(min_length=5, max_length=140)
    description: str = Field(min_length=10, max_length=5000)


class NotificationRead(BaseModel):
    read: bool = True


class UserResponse(BaseModel):
    id: int
    full_name: str
    email: str
    role: str

    model_config = ConfigDict(from_attributes=True)
