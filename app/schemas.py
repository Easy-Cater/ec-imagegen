from pydantic import BaseModel


class CreateRestyleBatchRequest(BaseModel):
    extra_styling: str | None = None
    # the photo itself arrives as multipart UploadFile in the router, not here


class JobOut(BaseModel):
    model_config = {"protected_namespaces": (), "from_attributes": True}

    id: int
    batch_id: str
    status: str
    # Position of this attempt within its batch (0 = the merchant's initial
    # upload, 1 = their first Regenerate click, etc). Also the index into
    # prompt_builder.RESTYLE_VARIATION_STYLES used for this attempt.
    # Frontend uses this + MAX_IMAGES_PER_BATCH to show "Attempt 2 of 4" and
    # to know when to grey out the Regenerate button.
    variation_index: int
    model_used: str | None
    source_image_path: str | None
    is_selected: bool
    image_path: str | None
    cost_usd: float | None
    error_message: str | None


class RegenerateRestyleRequest(BaseModel):
    batch_id: str


class SelectRestyleRequest(BaseModel):
    job_id: int