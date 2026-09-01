from fastapi import FastAPI
from typing import Optional, Annotated
from pydantic import BaseModel, Field, ConfigDict
from pymongo import AsyncMongoClient
from pymongo import ReturnDocument
from datetime import datetime
from pydantic.functional_validators import BeforeValidator

app = FastAPI()

client = AsyncMongoClient("STRING")
db = client["bootcamp"]
trx_collection = db["trx_collection"]

PyObjectId = Annotated[str, BeforeValidator(str)]

class RequestNewTransaction(BaseModel):
    amount: int
    method: str
    desc: str

class Transaction(BaseModel):
    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    date: datetime
    amount: int
    method: str
    desc: str
    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        validate_assignment=True
    )

@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.post("/transaction/add")
async def add_transaction(request_body: RequestNewTransaction):
    data = Transaction(date=datetime.now(), amount=request_body.amount, method=request_body.method, desc=request_body.desc)
    result = await trx_collection.insert_one(data.model_dump(by_alias=True, exclude=["id"]))
    
    data.id = result.inserted_id

    return data
