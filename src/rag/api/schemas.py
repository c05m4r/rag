from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)


class SourceChunk(BaseModel):
    source: str
    preview: str


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceChunk]


class IngestRequest(BaseModel):
    force_reindex: bool = False


class IngestResponse(BaseModel):
    status: str
    chunks_indexed: int
