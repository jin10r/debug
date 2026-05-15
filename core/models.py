from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel


class EventsFilterRequest(BaseModel):
    """Request model for filtering events with optional parameters."""
    since: Optional[datetime] = None
    time_filter: Optional[int] = None
    layers: Optional[List[str]] = None