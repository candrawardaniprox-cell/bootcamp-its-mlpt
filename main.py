from fastapi import FastAPI
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from beanie import Document, init_beanie
from pymongo import AsyncMongoClient

app = FastAPI()

class Transaction(Document):
    date: datetime
    amount: int
    method: str
    desc: str
    trx_type: str

    class Settings:
        name = "trx_collection"

class RequestNewTransaction(BaseModel):
    amount: int
    method: str
    desc: str
    trx_type: str

@app.on_event("startup")
async def init_db():
    client = AsyncMongoClient("string")
    await init_beanie(database=client.bootcamp, document_models=[Transaction])

@app.post("/transaction/add")
async def add_transaction(request_body: RequestNewTransaction):
    trx = Transaction(date=datetime.now(), amount=request_body.amount, method=request_body.method, desc=request_body.desc, trx_type=request_body.trx_type)
    await trx.insert()
    return trx

@app.get("/transaction")
async def get_transaction(start_date: datetime, end_date: datetime):
    return await Transaction.find(
        Transaction.date >= start_date, Transaction.date <= end_date
    ).to_list()

@app.get("/transaction/summary")
async def summary_by_method(year: int, month: int):
    start = datetime(year, month, 1)
    # first day of next month
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": start, 
                    "$lt": end
                }
            }
        },
        {
            "$group": {
                "_id": "$trx_type",
                "total_amount": {
                    "$sum": "$amount"
                },
                "count": {
                    "$sum": 1
                },
            }
        }
    ]

    return await Transaction.aggregate(pipeline).to_list()
