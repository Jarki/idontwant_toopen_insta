import datetime

import pydantic


class IgReel(pydantic.BaseModel):
    id: str
    title: str
    description: str|None
    filepath: str
    url: str
    comments: str
    like_count: int
    created_at: datetime.datetime = pydantic.Field(default_factory=datetime.datetime.now)