from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field, field_validator


class EventsFilterRequest(BaseModel):
    """Request model for filtering events with optional parameters.

    Границы — defense-in-depth против абьюза (огромные/отрицательные значения,
    гигантские списки); не отбрасывают легитимные значения.
    """
    since: Optional[datetime] = None
    # минуты; 0 = без ограничения по времени. Верхняя граница ~31 дней.
    time_filter: Optional[int] = Field(default=None, ge=0, le=44640)
    # реально 4 слоя; ограничение длины списка страхует от мусора.
    layers: Optional[List[str]] = Field(default=None, max_length=16)

    @field_validator('layers')
    @classmethod
    def validate_layers_list(cls, v):
        if v is None:
            return v
        for i, layer in enumerate(v):
            if not isinstance(layer, str) or not layer.strip():
                raise ValueError(f'layers[{i}] must be a non-empty string')
            layer = layer.strip()
            if len(layer) > 32:
                raise ValueError(f'layers[{i}] exceeds maximum length of 32')
        return v