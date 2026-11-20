from sqlmodel import Field, SQLModel


class Room(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    latitude: float
    longitude: float
    width_m: float
    depth_m: float
    height_m: float
