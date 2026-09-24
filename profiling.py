from fastapi import FastAPI, Query
from pydantic import Field
from datetime import datetime
from dateutil.relativedelta import relativedelta
from beanie import Document, init_beanie
from pymongo import AsyncMongoClient
from enum import Enum
from typing import Optional
from dotenv import load_dotenv
import os

load_dotenv()
app = FastAPI(title="Ferdi Finance - Profiling Service")

# --- KONFIGURASI ANALISIS ---
JUMLAH_BULAN_BASELINE = 12       # Pembagi baseline (rata-rata 12 bulan)
SERTAKAN_BULAN_PARSIAL = True    # True  : jendela terlama yang datanya baru sebagian tetap dihitung sebagai
                                 #         bulan aktual (sesuai skenario 8 bulan aktual + 4 duplikat).
                                 # False : hanya jendela yang penuh yang dihitung, sisanya diduplikasi.


# --- MODEL DATABASE (READ-ONLY VIEW) ---
# Profiling Service hanya membaca collection milik Transaction Service.
# Yang dipakai untuk analisis hanya `date` dan `amount`; field lain dibuat longgar (str)
# supaya kalau Enum di Transaction Service berubah, service ini tidak ikut error.
class Transaction(Document):
    date: datetime
    amount: int
    method: Optional[str] = None
    desc: Optional[str] = None
    trx_type: Optional[str] = None
    category: Optional[str] = None

    class Settings:
        name = "trx_collection"   # harus sama dengan Transaction Service


@app.on_event("startup")
async def init_db():
    # Harus menunjuk ke database yang sama dengan Transaction Service
    mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    client = AsyncMongoClient(mongo_uri)
    await init_beanie(database=client.bootcamp, document_models=[Transaction])


# --- PESAN & KONSTANTA ---
PESAN_HEMAT = (
    "Kenyamanan masa depan dibeli dengan kedisiplinan hari ini. Pertahankan sabarmu, "
    "karena kebebasan finansial jauh lebih nikmat daripada kepuasan sesaat ketika checkout barang."
)
PESAN_BOROS = (
    "Barang siapa yang tidak mampu menahan nafsunya berbelanja hari ini, maka ia harus siap menahan "
    "perihnya kebangkrutan dan jeratan hutang di masa depan. Tobatlah, kurangi gaya hidupmu "
    "sebelum uangmu yang mengurangi jalan hidupmu."
)

NAMA_BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni",
              "Juli", "Agustus", "September", "Oktober", "November", "Desember"]


# --- FUNGSI PERHITUNGAN ---
def hitung_persentase(selisih: float, y: float) -> float:
    """Persentase selisih terhadap rata-rata 12 bulan (Y): selisih / Y x 100."""
    return round(selisih / y * 100, 1) if y else 0.0


def buat_keterangan(arah: str, selisih: int, persen: float) -> str:
    rupiah = f"{selisih:,}".replace(",", ".")
    persen_txt = f"{persen:.1f}".replace(".", ",")
    return f"{arah} Rp{rupiah} ({persen_txt}%) dari batas kewajaran"


def tentukan_status(x: float, y: float):
    """Kembalikan (status, pesan, arah_selisih). x < y = hemat, x >= y = boros."""
    if x < y:
        return "Hemat / Big Saver", PESAN_HEMAT, "hemat"
    return "Boros / Reckless Spender", PESAN_BOROS, "lebih"


async def muat_pengeluaran(sekarang: datetime) -> list:
    """Ambil semua pengeluaran (amount < 0) sampai 'sekarang' sebagai list of (datetime, amount)."""
    transaksi = await Transaction.find(
        Transaction.amount < 0,
        Transaction.date <= sekarang,
    ).to_list()
    return [(t.date, t.amount) for t in transaksi]


def hitung_profil(pengeluaran: list, sekarang: datetime, tanggal_terlama: datetime) -> dict:
    """
    pengeluaran     : list of (datetime, amount) dengan amount < 0
    sekarang        : waktu saat API dipanggil
    tanggal_terlama : tanggal transaksi paling awal yang ada di database
    """
    # 1. Variabel X: rolling window 1 bulan ke belakang dari sekarang
    awal_x = sekarang - relativedelta(months=1)
    x = sum(abs(amt) for tgl, amt in pengeluaran if awal_x <= tgl <= sekarang)

    # 2. Data historis: jendela 1 bulan berurutan mundur dari awal_x (indeks 0 = terbaru)
    #    Sebuah jendela dihitung sebagai "bulan aktual" jika masih ada data di dalamnya.
    total_per_bulan = []
    for i in range(1, JUMLAH_BULAN_BASELINE + 1):
        awal = awal_x - relativedelta(months=i)
        akhir = awal_x - relativedelta(months=i - 1)

        ada_data = (akhir > tanggal_terlama) if SERTAKAN_BULAN_PARSIAL else (awal >= tanggal_terlama)
        if not ada_data:
            break

        total = sum(abs(amt) for tgl, amt in pengeluaran if awal <= tgl < akhir)
        total_per_bulan.append(total)

    jumlah_aktual = len(total_per_bulan)
    if jumlah_aktual == 0:
        return {"data_cukup": False, "x": x}

    # Imputasi: duplikat bulan-bulan terlama untuk menutup kekurangan sampai 12 bulan
    # (mis. 8 aktual -> 4 bulan terlama diduplikasi). Jika data sangat sedikit, diulang bergiliran.
    jumlah_duplikat = JUMLAH_BULAN_BASELINE - jumlah_aktual
    bulan_terlama_dulu = list(reversed(total_per_bulan))   # urut dari bulan terlama
    duplikat = [bulan_terlama_dulu[i % jumlah_aktual] for i in range(jumlah_duplikat)]

    data_12_bulan = total_per_bulan + duplikat
    total_12_bulan = sum(data_12_bulan)

    # 3. Variabel Y: rata-rata pengeluaran per bulan
    y = total_12_bulan / JUMLAH_BULAN_BASELINE

    return {
        "data_cukup": True,
        "x": x,
        "y": y,
        "awal_x": awal_x,
        "jumlah_aktual": jumlah_aktual,
        "jumlah_duplikat": jumlah_duplikat,
        "total_12_bulan": total_12_bulan,
    }


class PeriodeType(str, Enum):
    satu_bulan = "1_bulan"       # 1 bulan terakhir (rolling window dari waktu API dipanggil)
    sembilan_bulan = "9_bulan"   # riwayat: bulan-bulan kalender yang sudah selesai + 1 bulan terakhir


# 9 periode = 8 bulan kalender selesai (mis. Januari s/d Agustus) + 1 bulan terakhir (rolling)
JUMLAH_PERIODE_RIWAYAT = 9


def buat_entri(id_periode: str, label: str, tipe: str, mulai: datetime, selesai: datetime,
               total: int, y: float, data_lengkap: bool = True) -> dict:
    status, pesan, arah = tentukan_status(total, y)
    selisih = round(abs(total - y))
    persen = hitung_persentase(abs(total - y), y)
    return {
        "periode": id_periode,
        "label": label,
        "tipe": tipe,                              # "kalender" atau "rolling"
        "tanggal_mulai": f"{mulai:%Y-%m-%d}",
        "tanggal_selesai": f"{selesai:%Y-%m-%d}",
        "status": status,
        "pengeluaran": total,
        "selisih": selisih,
        "persentase_selisih": persen,              # selisih / rata-rata 12 bulan x 100
        "keterangan_selisih": buat_keterangan(arah, selisih, persen),
        "pesan": pesan,
        # False jika data bulan ini baru mulai di tengah bulan (angkanya bisa lebih kecil dari aslinya)
        "data_lengkap": data_lengkap,
    }


def hitung_riwayat_bulanan(pengeluaran: list, sekarang: datetime, tanggal_terlama: datetime,
                           y: float, maks_bulan: int) -> list:
    """
    Menilai setiap BULAN KALENDER yang sudah selesai (dari bulan data terawal sampai bulan lalu)
    terhadap batas kewajaran Y yang sama dengan opsi 1 bulan.
    """
    awal_bulan_ini = sekarang.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    kursor = tanggal_terlama.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    riwayat = []
    while kursor < awal_bulan_ini:
        akhir = kursor + relativedelta(months=1)
        total = sum(abs(amt) for tgl, amt in pengeluaran if kursor <= tgl < akhir)
        parsial = (kursor.year == tanggal_terlama.year and kursor.month == tanggal_terlama.month
                   and tanggal_terlama.day > 1)
        riwayat.append(buat_entri(
            id_periode=f"{kursor:%Y-%m}",
            label=f"{NAMA_BULAN[kursor.month - 1]} {kursor.year}",
            tipe="kalender",
            mulai=kursor,
            selesai=akhir - relativedelta(days=1),
            total=total,
            y=y,
            data_lengkap=not parsial,
        ))
        kursor = akhir

    return riwayat[-maks_bulan:]


def susun_riwayat_9_bulan(pengeluaran: list, sekarang: datetime, tanggal_terlama: datetime, hasil: dict) -> dict:
    x, y = hasil["x"], hasil["y"]

    # 8 bulan kalender selesai (mis. Januari s/d Agustus)
    riwayat = hitung_riwayat_bulanan(pengeluaran, sekarang, tanggal_terlama, y, JUMLAH_PERIODE_RIWAYAT - 1)

    # + 1 bulan terakhir (rolling), karena data berjalan sampai hari ini (mis. 22 September)
    riwayat.append(buat_entri(
        id_periode="1_bulan_terakhir",
        label="1 Bulan Terakhir",
        tipe="rolling",
        mulai=hasil["awal_x"],
        selesai=sekarang,
        total=x,
        y=y,
    ))

    return {
        "periode": PeriodeType.sembilan_bulan.value,
        "batas_kewajaran_bulanan": round(y),
        "ringkasan": {
            "jumlah_periode": len(riwayat),
            "periode_hemat": sum(1 for r in riwayat if r["status"].startswith("Hemat")),
            "periode_boros": sum(1 for r in riwayat if r["status"].startswith("Boros")),
        },
        "riwayat": riwayat,
    }


# --- 1. ALERT API (dipanggil Transaction Service setelah transaksi baru dicatat) ---
@app.get("/profiling/alert")
async def get_alert():
    """
    Ringkasan singkat status Hemat/Boros untuk 1 bulan terakhir.
    Return null jika data belum cukup untuk dianalisis.
    """
    sekarang = datetime.now()
    transaksi_pertama = await Transaction.find_all().sort(Transaction.date).first_or_none()
    if transaksi_pertama is None:
        return None

    pengeluaran = await muat_pengeluaran(sekarang)
    hasil = hitung_profil(pengeluaran, sekarang, transaksi_pertama.date)
    if not hasil["data_cukup"]:
        return None

    status, pesan, _ = tentukan_status(hasil["x"], hasil["y"])
    return {"status": status, "pesan": pesan}


# --- 2. ANALISIS PROFIL (Boros vs Hemat) API ---
@app.get("/profiling/analysis")
async def analyze_profile(
    periode: PeriodeType = Query(
        PeriodeType.satu_bulan,
        description="1_bulan = 1 bulan terakhir (rolling). 9_bulan = riwayat per bulan (8 bulan kalender selesai + 1 bulan terakhir)."
    )
):
    sekarang = datetime.now()

    # Transaksi paling awal di database (penentu berapa bulan data aktual yang tersedia)
    transaksi_pertama = await Transaction.find_all().sort(Transaction.date).first_or_none()
    if transaksi_pertama is None:
        return {
            "status": "Data belum cukup",
            "pengeluaran_bulan_ini": 0,
            "batas_kewajaran_bulanan": 0,
            "selisih": 0,
            "pesan": "Belum ada transaksi di database. Upload data Excel atau catat transaksi terlebih dahulu."
        }

    pengeluaran = await muat_pengeluaran(sekarang)
    hasil = hitung_profil(pengeluaran, sekarang, transaksi_pertama.date)

    if not hasil["data_cukup"]:
        return {
            "status": "Data belum cukup",
            "pengeluaran_bulan_ini": hasil["x"],
            "batas_kewajaran_bulanan": 0,
            "selisih": 0,
            "pesan": "Belum ada data historis sebelum 1 bulan terakhir untuk dijadikan baseline."
        }

    # --- Opsi 9 bulan: riwayat status per bulan ---
    if periode == PeriodeType.sembilan_bulan:
        return susun_riwayat_9_bulan(pengeluaran, sekarang, transaksi_pertama.date, hasil)

    # --- Opsi 1 bulan: analisis rolling 1 bulan terakhir ---
    x, y = hasil["x"], hasil["y"]
    status, pesan, arah = tentukan_status(x, y)
    persen = hitung_persentase(abs(x - y), y)

    return {
        "status": status,
        "pengeluaran_bulan_ini": x,
        "batas_kewajaran_bulanan": round(y),
        "selisih": round(abs(x - y)),
        "persentase_selisih": persen,   # selisih / batas kewajaran (rata-rata 12 bulan) x 100
        "pesan": pesan,
        # Info tambahan untuk transparansi perhitungan (boleh dihapus)
        "detail": {
            "periode": PeriodeType.satu_bulan.value,
            "keterangan_selisih": buat_keterangan(arah, round(abs(x - y)), persen),
            "periode_x": f"{hasil['awal_x']:%Y-%m-%d %H:%M} s/d {sekarang:%Y-%m-%d %H:%M}",
            "bulan_data_aktual": hasil["jumlah_aktual"],
            "bulan_data_duplikat": hasil["jumlah_duplikat"],
            "total_pengeluaran_12_bulan": hasil["total_12_bulan"],
        }
    }