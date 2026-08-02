# -*- coding: utf-8 -*-
"""
build_residue_db.py — 把 residue_limits.csv 匯入 SQLite（新增 residue_limits 資料表）
執行：python build_residue_db.py
沿用同一個 pesticides.db，不影響其他資料表
"""
import sqlite3
import pandas as pd
import os

CSV_FILE = "residue_limits.csv"
DB_FILE = "pesticides.db"
TABLE_NAME = "residue_limits"


def main():
    if not os.path.exists(CSV_FILE):
        print(f"❌ 找不到 {CSV_FILE}，請先執行 scrape_residue.py")
        return
    if not os.path.exists(DB_FILE):
        print(f"❌ 找不到 {DB_FILE}，請先執行過 build_db.py 建立主資料庫")
        return

    df = pd.read_csv(CSV_FILE, encoding="utf-8-sig", dtype=str).fillna("")
    df.insert(0, "id", range(1, len(df) + 1))

    conn = sqlite3.connect(DB_FILE)
    df.to_sql(TABLE_NAME, conn, if_exists="replace", index=False)

    cur = conn.cursor()
    cur.execute(f'CREATE INDEX IF NOT EXISTS idx_residue_category ON {TABLE_NAME}("用途分類")')
    conn.commit()
    count = cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]
    conn.close()

    print(f"✅ 完成！{TABLE_NAME} 資料表：{count} 筆")
    print("下一步：python build_residue_index.py")


if __name__ == "__main__":
    main()
