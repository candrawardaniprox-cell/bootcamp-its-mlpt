from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from pydantic import BaseModel, Field
from datetime import datetime, date, timedelta
from collections import defaultdict
from beanie import Document, init_beanie, PydanticObjectId
from pymongo import AsyncMongoClient
from enum import Enum
from typing import Optional
from dotenv import load_dotenv
import httpx
import pandas as pd
import os

load_dotenv()
app = FastAPI(title="Ferdi Finance - Transaction Service")

# --- KONFIGURASI ---
# Alamat Profiling Service (dipanggil setelah transaksi baru dicatat)
PROFILING_SERVICE_URL = os.getenv("PROFILING_SERVICE_URL", "http://localhost:8001")
PROFILING_TIMEOUT_DETIK = 3.0


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
    uncategorized = "uncategorized"


# --- MODEL DATABASE ---
class Transaction(Document):
    date: datetime
    # Pengeluaran = negatif, pemasukan = positif
    amount: int
    method: MethodType
    desc: str
    trx_type: TrxType
    category: CategoryType = CategoryType.uncategorized

    class Settings:
        name = "trx_collection"


# --- MODEL REQUEST ---
class RequestNewTransaction(BaseModel):
    # Boleh negatif (pengeluaran) maupun positif (pemasukan)
    amount: int = Field(description="Negatif = pengeluaran, positif = pemasukan")
    method: MethodType
    desc: str
    # Opsional: jika kosong, ditentukan otomatis dari tanda amount
    trx_type: Optional[TrxType] = None
    category: CategoryType = CategoryType.uncategorized
    # Custom date (YYYY-MM-DD), opsional
    trx_date: Optional[date] = None


def tentukan_trx_type(amount: int, trx_type: Optional[TrxType] = None) -> TrxType:
    """amount < 0 -> pengeluaran, amount > 0 -> pemasukan."""
    if amount == 0:
        raise HTTPException(status_code=400, detail="Nominal tidak boleh 0")
    if trx_type is not None:
        return trx_type
    return TrxType.pengeluaran if amount < 0 else TrxType.pemasukan


async def ambil_alert_profiling() -> Optional[dict]:
    """
    Minta status profiling (Hemat/Boros) ke Profiling Service.
    Jika Profiling Service mati / lambat / datanya belum cukup -> return None,
    supaya pencatatan transaksi TIDAK ikut gagal.
    """
    try:
        async with httpx.AsyncClient(timeout=PROFILING_TIMEOUT_DETIK) as client:
            resp = await client.get(f"{PROFILING_SERVICE_URL}/profiling/alert")
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return None


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
        trx_type=tentukan_trx_type(request.amount, request.trx_type),
        category=request.category
    )
    await trx.insert()

    # Trigger profiling: tanya Profiling Service (kalau gagal, alert = None)
    alert_data = await ambil_alert_profiling()

    return {
        "message": "Transaksi berhasil dicatat",
        "data": trx,
        "alert_profiling": alert_data
    }


# --- 2. UPDATE API ---
@app.put("/transaction/{trx_id}")
async def update_transaction(trx_id: PydanticObjectId, request: RequestNewTransaction):
    trx = await Transaction.get(trx_id)
    if not trx:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")

    trx.amount = request.amount
    trx.method = request.method
    trx.desc = request.desc
    trx.trx_type = tentukan_trx_type(request.amount, request.trx_type)
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
# Kolom Excel: datetime, amount, payment_method, description
# (nama kolom lama: date, method, desc tetap diterima)
KOLOM_EXCEL_KE_MODEL = {
    "datetime": "date",
    "payment_method": "method",
    "description": "desc",
}
KOLOM_WAJIB = {"date", "amount", "method", "desc"}


@app.post("/transaction/upload")
async def upload_excel(file: UploadFile = File(...)):
    if not (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
        raise HTTPException(status_code=400, detail="Mohon unggah file Excel dengan format .xlsx atau .xls")

    try:
        df = pd.read_excel(file.file)
        df.columns = [str(c).strip().lower() for c in df.columns]
        df = df.rename(columns=KOLOM_EXCEL_KE_MODEL)

        kolom_kurang = KOLOM_WAJIB - set(df.columns)
        if kolom_kurang:
            raise HTTPException(
                status_code=400,
                detail=f"Kolom Excel tidak lengkap. Kolom yang kurang: {sorted(kolom_kurang)}. "
                       f"Kolom yang dibutuhkan: datetime, amount, payment_method, description"
            )

        df = df.fillna('')

        # Parsing semua baris dulu; data lama baru dihapus jika ada baris valid
        documents = []
        baris_dilewati = []

        for idx, row in enumerate(df.to_dict(orient='records')):
            try:
                amount = int(row['amount'])
                if amount == 0:
                    raise ValueError("amount = 0")

                trx = Transaction(
                    date=pd.to_datetime(row['date']).to_pydatetime(),
                    amount=amount,
                    method=MethodType(str(row['method']).strip().lower()),
                    desc=str(row['desc']).strip(),
                    # Otomatis dari tanda nominal
                    trx_type=TrxType.pengeluaran if amount < 0 else TrxType.pemasukan,
                    category=CategoryType.uncategorized
                )
                documents.append(trx)
            except Exception as e:
                # Lewati baris yang datanya tidak valid sesuai Enum/aturan
                baris_dilewati.append({"baris_excel": idx + 2, "alasan": str(e)[:100]})

        if not documents:
            raise HTTPException(status_code=400, detail="Tidak ada baris valid di file Excel, data lama tidak diubah.")

        # Hapus seluruh data lama, lalu masukkan data baru sekaligus (jauh lebih cepat dari insert satu-satu)
        await Transaction.find_all().delete()
        await Transaction.insert_many(documents)

        return {
            "message": f"Berhasil memigrasikan {len(documents)} transaksi dari file Excel",
            "jumlah_dilewati": len(baris_dilewati),
            "contoh_baris_dilewati": baris_dilewati[:10],
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal membaca file Excel: {str(e)}")


# --- 5. SUMMARY API ---
def ringkas(transaksi: list) -> dict:
    """Hitung total pemasukan, pengeluaran, dan saldo bersih dari sekumpulan transaksi."""
    pemasukan = sum(t.amount for t in transaksi if t.amount > 0)
    pengeluaran = sum(abs(t.amount) for t in transaksi if t.amount < 0)
    return {
        "jumlah_transaksi": len(transaksi),
        "total_pemasukan": pemasukan,
        "total_pengeluaran": pengeluaran,
        "saldo_bersih": pemasukan - pengeluaran,
    }


def urutkan_by_pengeluaran(kelompok: dict) -> dict:
    """Urutkan hasil ringkasan dari pengeluaran terbesar."""
    return dict(sorted(kelompok.items(), key=lambda kv: kv[1]["total_pengeluaran"], reverse=True))


@app.get("/transaction/summary")
async def get_summary(
    start_date: Optional[date] = Query(None, description="Tanggal mulai (YYYY-MM-DD), opsional. Kosong = dari transaksi paling awal."),
    end_date: Optional[date] = Query(None, description="Tanggal akhir (YYYY-MM-DD, inklusif), opsional. Kosong = sampai transaksi terakhir."),
):
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date tidak boleh lebih besar dari end_date")

    kondisi = []
    if start_date:
        kondisi.append(Transaction.date >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        # end_date inklusif -> batas atas = awal hari berikutnya
        kondisi.append(Transaction.date < datetime.combine(end_date + timedelta(days=1), datetime.min.time()))

    transaksi = await Transaction.find(*kondisi).to_list()

    per_metode = defaultdict(list)
    per_kategori = defaultdict(list)
    for t in transaksi:
        per_metode[t.method.value].append(t)
        per_kategori[t.category.value].append(t)

    return {
        "periode": {
            "mulai": str(start_date) if start_date else None,
            "selesai": str(end_date) if end_date else None,
        },
        **ringkas(transaksi),
        "per_metode": urutkan_by_pengeluaran({k: ringkas(v) for k, v in per_metode.items()}),
        "per_kategori": urutkan_by_pengeluaran({k: ringkas(v) for k, v in per_kategori.items()}),
    }