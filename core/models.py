from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field


class EventsFilterRequest(BaseModel):
    """Request model for filtering events with optional parameters.

    Границы — defense-in-depth против абъюза (огромные/отрицательные значения,
    гигантские списки); не отбрасывают легитимные значения.
    """
    since: Optional[datetime] = None
    # минуты; 0 = без ограничения по времени. Верхняя граница ~31 день.
    time_filter: Optional[int] = Field(default=None, ge=0, le=44640)
    # реально 4 слоя; ограничение длины списка страхует от мусора.
    layers: Optional[List[str]] = Field(default=None, max_length=16)