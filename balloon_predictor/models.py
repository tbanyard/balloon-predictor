from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PredictionRequest(StrictModel):
    dataset: str = "demo"
    launch_datetime: datetime
    flight_name: str = Field(default="Flight", max_length=60)
    flight_mode: Literal["full", "ascent"] = "full"
    launch_latitude: float = Field(default=52.817208, ge=-85, le=85)
    launch_longitude: float = Field(default=-4.125, ge=-180, le=360)
    launch_altitude: float = Field(default=100, ge=-500, le=55000)
    ascent_rate: float = Field(default=5, gt=0, le=30)
    burst_altitude: float = Field(default=25000, gt=0, le=55000)
    descent_rate: float = Field(default=5, gt=0, le=50)
    descent_model: Literal["density", "constant"] = "density"
    landing_mode: Literal["terrain", "fixed"] = "terrain"
    landing_altitude: float = Field(default=0, ge=-500, le=10000)
    use_vertical_wind: bool = False
    time_step: float = Field(default=10, ge=1, le=60)
    max_duration_hours: float = Field(default=12, gt=0, le=48)
    ensemble_members: int = Field(default=0, ge=0, le=50)
    ascent_sigma: float = Field(default=0.3, ge=0, le=5)
    burst_sigma: float = Field(default=500, ge=0, le=5000)
    descent_sigma: float = Field(default=0.3, ge=0, le=5)
    wind_sigma: float = Field(default=0.5, ge=0, le=10)
    seed: int = Field(default=42, ge=0, le=2**32 - 1)

    @field_validator("launch_datetime")
    @classmethod
    def utc_datetime(cls, value):
        if value.tzinfo is None:
            raise ValueError("Specify a timezone, for example 2024-01-15T12:00:00Z.")
        return value.astimezone(timezone.utc)

    @field_validator("launch_longitude")
    @classmethod
    def wrap_longitude(cls, value):
        return (value + 180) % 360 - 180

    @model_validator(mode="after")
    def sensible_flight(self):
        if self.burst_altitude <= self.launch_altitude:
            raise ValueError("Burst altitude must exceed launch altitude (both AMSL).")
        if self.landing_mode == "fixed" and self.landing_altitude >= self.burst_altitude:
            raise ValueError("Landing altitude must be below burst altitude.")
        return self


class BatchPredictionRequest(StrictModel):
    flights: list[PredictionRequest] = Field(min_length=1, max_length=20)
    duration_hours: float = Field(default=24, gt=0, le=168)
    interval_hours: float = Field(default=1, ge=.25, le=24)

    @model_validator(mode="after")
    def limit_work(self):
        import math
        count = math.ceil(self.duration_hours / self.interval_hours - 1e-10)
        if count * len(self.flights) > 256:
            raise ValueError("A series can contain at most 256 launches across all launch points.")
        if count * sum(1+f.ensemble_members for f in self.flights) > 1024:
            raise ValueError("Reduce launch count or sensitivity members: maximum 1024 calculated flights per series.")
        return self


class DownloadRequest(StrictModel):
    source: Literal["era5", "era5t", "ifs"]
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    start: datetime
    end: datetime
    area: tuple[float, float, float, float] = (60, -12, 48, 5)
    grid: float = Field(default=0.25, ge=0.25, le=2)
    cycle: datetime | None = None
    surface: bool = True
    refresh: bool = False

    @field_validator("start", "end", "cycle")
    @classmethod
    def utc_datetime(cls, value):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Times must include Z or a UTC offset.")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_window(self):
        if self.end <= self.start:
            raise ValueError("End must be after start.")
        if (self.end - self.start).total_seconds() > 72 * 3600:
            raise ValueError("Download at most 72 hours per dataset; use several datasets for longer studies.")
        n, w, s, e = self.area
        if not (-90 <= s < n <= 90 and -180 <= w < e <= 180):
            raise ValueError("Area is [north, west, south, east]; use a non-dateline-crossing area.")
        if self.cycle and (self.cycle.hour not in (0, 6, 12, 18) or self.cycle.minute or self.cycle.second):
            raise ValueError("IFS cycle must be 00, 06, 12 or 18 UTC on the hour.")
        return self
