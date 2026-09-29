import sqlite3
from datetime import date
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

DB = '/content/drive/MyDrive/ProjetPCO/leak_detection.db'
app = FastAPI(title='Leak detection API')


def query(sql, params=()):
    try:
        con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)   # read-only
        con.row_factory = sqlite3.Row
        rows = con.execute(sql, params).fetchall()
        con.close()
    except sqlite3.Error as e:
        print(f'Database error: {e}')
        raise HTTPException(status_code=503, detail='Database unavailable')
    return [dict(r) for r in rows]


def execute(sql, params=()):
    try:
        con = sqlite3.connect(DB)                                  # read-write
        cur = con.execute(sql, params)
        con.commit()
        con.close()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail='Detection already exists')
    except sqlite3.Error as e:
        print(f'Database error: {e}')
        raise HTTPException(status_code=503, detail='Database unavailable')


execute("""CREATE TABLE IF NOT EXISTS detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    counter_id INTEGER NOT NULL,
    date_timestamp DATE NOT NULL,
    score REAL NOT NULL,
    threshold REAL NOT NULL,
    model_version TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(counter_id, date_timestamp, model_version))""")


@app.get('/counters')
def list_counters():
    return query("""SELECT counter_id, COUNT(daily_volume) AS nb_days,
                           MIN(date_timestamp) AS start, MAX(date_timestamp) AS end
                    FROM perf_daily GROUP BY counter_id""")


@app.get('/counters/{counter_id}/daily')
def get_daily(counter_id: int, start: date = date(2000, 1, 1), end: date = date(2100, 1, 1)):
    if start > end:
        raise HTTPException(status_code=400, detail='start must be before end')
    rows = query("""SELECT date(date_timestamp) AS date, daily_volume, max_debit, min_debit, night_debit
                    FROM perf_daily
                    WHERE counter_id = ? AND date(date_timestamp) BETWEEN ? AND ?
                    ORDER BY date_timestamp""", (counter_id, start.isoformat(), end.isoformat()))
    if not rows:
        raise HTTPException(status_code=404, detail=f'No data for counter {counter_id}')
    return rows


@app.get('/counters/{counter_id}/labels')
def get_labels(counter_id: int, category: str | None = None, merge: bool = False):
    sql = """SELECT DISTINCT date(start_period) AS start, date(end_period) AS end, label_category
             FROM labeled_periods WHERE counter_id = ?"""
    params = [counter_id]
    if category:
        sql += " AND label_category = ?"
        params.append(category)
    rows = query(sql + " ORDER BY label_category, start_period", tuple(params))
    if merge:
        merged = []
        for r in rows:
            last = merged[-1] if merged else None
            if last and r['label_category'] == last['label_category'] and r['start'] <= last['end']:
                last['end'] = max(last['end'], r['end'])   # overlap: extend the previous event
            else:
                merged.append(r)
        rows = merged
    return rows


class Detection(BaseModel):
    counter_id: int
    date_timestamp: date
    score: float
    threshold: float
    model_version: str = Field(min_length=1)


@app.post('/detections', status_code=201)
def create_detection(d: Detection):
    new_id = execute("""INSERT INTO detections (counter_id, date_timestamp, score, threshold, model_version)
                        VALUES (?, ?, ?, ?, ?)""",
                     (d.counter_id, d.date_timestamp.isoformat(), d.score, d.threshold, d.model_version))
    return {'id': new_id, **d.model_dump(mode='json')}


@app.get('/detections')
def list_detections(counter_id: int | None = None):
    if counter_id is None:
        return query("SELECT * FROM detections ORDER BY date_timestamp")
    return query("SELECT * FROM detections WHERE counter_id = ? ORDER BY date_timestamp", (counter_id,))
