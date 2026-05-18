from pydantic import BaseModel, ConfigDict


class InstrumentBase(BaseModel):
    ticker: str
    name: str
    market: str | None = None
    board: str | None = None
    currency: str | None = None
    is_active: bool = True


class InstrumentCreate(InstrumentBase):
    pass


class InstrumentRead(InstrumentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
