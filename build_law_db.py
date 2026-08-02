# -*- coding: utf-8 -*-
"""
build_law_db.py — 把 law_articles.csv 匯入 SQLite（新增 regulations 資料表）
執行：python build_law_db.py
沿用同一個 pesticides.db，不影響原本的 pesticides 資料表
"""
import sqlite3
import pandas as pd
import os

CSV_FILE = "law_articles.csv"
DB_FILE = "pesticides.db"
TABLE_NAME = "regulations"


def main():
    if not os.path.exists(CSV_FILE):
        print(f"❌ 找不到 {CSV_FILE}，請先執行 scrape_law.py")
        return
    if not os.path.exists(DB_FILE):
        print(f"❌ 找不到 {DB_FILE}，請先執行過 build_db.py 建立主資料庫")
        return

    df = pd.read_csv(CSV_FILE, encoding="utf-8-sig", dtype=str).fillna("")
    df.insert(0, "id", range(1, len(df) + 1))

    conn = sqlite3.connect(DB_FILE)
    df.to_sql(TABLE_NAME, conn, if_exists="replace", index=False)

    cur = conn.cursor()
    cur.execute(f'CREATE INDEX IF NOT EXISTS idx_law_article ON {TABLE_NAME}("條號")')
    conn.commit()
    count = cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]
    conn.close()

    print(f"✅ 完成！{TABLE_NAME} 資料表：{count} 條法規條文")
    print("下一步：python build_law_index.py")


if __name__ == "__main__":
    main()
