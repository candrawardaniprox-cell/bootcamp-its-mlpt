from fastapi import FastAPI, HTTPException, UploadFile, File
from pydantic import BaseModel, Field
from datetime import datetime, date
from beanie import Document, init_beanie, PydanticObjectId
from pymongo import AsyncMongoClient
from enum import Enum
from typing import Optional, List
from dotenv import load_dotenv
import pandas as pd
import csv
import codecs
import os 

load_dotenv()
app = FastAPI(title="Ferdi Finance API")
MONGODB_URI = os.getenv("MONGODB_URI")

# --- ENUMS UNTUK VALIDASI LOGIS (Dropdown) ---
class TrxType(str, Enum):
    pemasukan = "pemasukan"
    pengeluaran = "pengeluaran"

class MethodType(str, Enum):
    cash = "cash"
    gopay = "gopay"
    bca = "bca"
    shopee = "shopee"
    mandiri = "mandiri"

class CategoryType(str, Enum):
    needs = "needs"
    wants = "wants"
    savings = "savings"
    income = "income"

# --- MODEL DATABASE ---
class Transaction(Document):
    date: datetime
    amount: int
    method: MethodType
    desc: str
    trx_type: TrxType
    category: CategoryType

    class Settings:
        name = "trx_collection"

# --- MODEL REQUEST ---
class RequestNewTransaction(BaseModel):
    # Minimum 1, tidak boleh negatif
    amount: int = Field(gt=0, description="Nominal minimal 1, tidak boleh negatif")
    method: MethodType
    desc: str
    trx_type: TrxType
    category: CategoryType
    # Custom date (YYYY-MM-DD), opsional
    trx_date: Optional[date] = None

@app.on_event("startup")
async def init_db():
    # Mengambil URL dari Environment Variable (Privasi aman, Ferdi bisa pakai DB-nya sendiri)
    mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    client = AsyncMongoClient(mongo_uri)
    await init_beanie(database=client.bootcamp, document_models=[Transaction])

# --- 1. CREATE API ---
@app.post("/transaction/add")
async def add_transaction(request: RequestNewTransaction):
    # Logika Tanggal: Gunakan input user, jika kosong gunakan waktu sekarang
    final_date = datetime.combine(request.trx_date, datetime.min.time()) if request.trx_date else datetime.now()
    
    trx = Transaction(
        date=final_date, 
        amount=request.amount, 
        method=request.method, 
        desc=request.desc, 
        trx_type=request.trx_type,
        category=request.category
    )
    await trx.insert()
    return {"message": "Transaksi berhasil dicatat", "data": trx}

# --- 2. UPDATE API ---
@app.put("/transaction/{trx_id}")
async def update_transaction(trx_id: PydanticObjectId, request: RequestNewTransaction):
    trx = await Transaction.get(trx_id)
    if not trx:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")
    
    trx.amount = request.amount
    trx.method = request.method
    trx.desc = request.desc
    trx.trx_type = request.trx_type
    trx.category = request.category
    if request.trx_date:
        trx.date = datetime.combine(request.trx_date, datetime.min.time())
        
    await trx.save()
    return {"message": "Transaksi berhasil diubah", "data": trx}

# --- 3. DELETE API ---
@app.delete("/transaction/{trx_id}")
async def delete_transaction(trx_id: PydanticObjectId):
    trx = await Transaction.get(trx_id)
    if not trx:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")
    await trx.delete()
    return {"message": "Transaksi berhasil dihapus"}

# --- 4. EXCEL MIGRATION API (Menggunakan Pandas) ---
@app.post("/transaction/upload")
async def upload_excel(file: UploadFile = File(...)):
    if not (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
        raise HTTPException(status_code=400, detail="Mohon unggah file Excel dengan format .xlsx atau .xls")
    
    try:
        df = pd.read_excel(file.file)
        df = df.fillna('')
        
        # Hapus seluruh data lama sebelum memigrasikan data baru
        await Transaction.find_all().delete()
        
        records = df.to_dict(orient='records')
        inserted_count = 0
        
        for row in records:
            try:
                # Handle format tanggal dari Excel (bisa berupa Timestamp Pandas atau string)
                if isinstance(row['date'], pd.Timestamp):
                    trx_date = row['date'].to_pydatetime()
                else:
                    trx_date = datetime.strptime(str(row['date']).split(' ')[0], "%Y-%m-%d")

                trx = Transaction(
                    date=trx_date,
                    amount=int(row['amount']),
                    method=MethodType(str(row['method']).strip().lower()),
                    desc=str(row['desc']),
                    trx_type=TrxType(str(row['trx_type']).strip().lower()),
                    category=CategoryType(str(row['category']).strip().lower())
                )
                await trx.insert()
                inserted_count += 1
            except Exception:
                # Lewati baris yang datanya tidak valid sesuai Enum/aturan
                continue 
                
        return {"message": f"Berhasil memigrasikan {inserted_count} transaksi dari file Excel"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal membaca file Excel: {str(e)}")
        
# --- 5. ANALISIS PROFIL (Boros vs Hemat) API ---
@app.get("/transaction/analysis")
async def analyze_profile(year: int, month: int):
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)

    # Ambil semua data di bulan tersebut
    transactions = await Transaction.find(
        Transaction.date >= start, Transaction.date < end
    ).to_list()

    total_income = sum(t.amount for t in transactions if t.trx_type == TrxType.pemasukan)
    total_wants = sum(t.amount for t in transactions if t.category == CategoryType.wants)
    total_savings = sum(t.amount for t in transactions if t.category == CategoryType.savings)
    
    # Hitung jumlah jajan kecil (Bocor halus < 50.000)
    micro_transactions = sum(1 for t in transactions if t.category == CategoryType.wants and t.amount < 50000)

    # Cegah error jika belum ada pemasukan
    if total_income == 0:
        return {"status": "Data belum cukup", "message": "Belum ada pemasukan bulan ini untuk dianalisis."}

    wants_ratio = total_wants / total_income
    savings_ratio = total_savings / total_income
    
    # Penilaian (Skoring Poin)
    points = 0
    if wants_ratio > 0.30: points += 1 # Terlalu banyak keinginan
    if savings_ratio < 0.10: points += 1 # Kurang menabung
    if micro_transactions > 15: points += 1 # Terlalu sering jajan kecil
    if total_income - (sum(t.amount for t in transactions if t.trx_type == TrxType.pengeluaran)) <= 0: points += 1 # Defisit

    # Penentuan Karakter
    if points <= 1:
        profile = "Big Saver (Hemat & Terencana)"
    elif points == 2:
        profile = "Waspada (Ada indikasi gaya hidup berlebih)"
    else:
        profile = "Reckless Spender (BOROS!)"

    return {
        "profil_ferdi": profile,
        "skor_keborosan": points,
        "detail": {
            "pemasukan": total_income,
            "rasio_keinginan": f"{wants_ratio:.1%}",
            "rasio_tabungan": f"{savings_ratio:.1%}",
            "jumlah_jajan_receh": micro_transactions
        }
    }