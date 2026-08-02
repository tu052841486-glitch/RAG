# -*- coding: utf-8 -*-
"""
build_db.py
把 all_pesticide_leaf.csv 匯入 SQLite 資料庫 (pesticides.db)

執行：python build_db.py
輸入：all_pesticide_leaf.csv（跟本檔案放同一個資料夾）
輸出：pesticides.db
"""

import sqlite3
import pandas as pd
import os

CSV_FILE = "all_pesticide_leaf.csv"
DB_FILE = "pesticides.db"
TABLE_NAME = "pesticides"


def main():
    if not os.path.exists(CSV_FILE):
        print(f"❌ 找不到 {CSV_FILE}，請確認它跟 build_db.py 放在同一個資料夾")
        return

    print(f"📥 讀取 {CSV_FILE} ...")
    df = pd.read_csv(CSV_FILE, encoding="utf-8-sig", dtype=str)
    df = df.fillna("")
    print(f"  → 共 {len(df)} 筆，欄位：{list(df.columns)}")

    # 清理欄位名稱：SQLite 對中文欄位沒問題，但避免奇怪符號
    df.columns = [c.strip() for c in df.columns]

    # 加上一個遞增主鍵 id，方便後續當作向量資料庫的 metadata 對照
    df.insert(0, "id", range(1, len(df) + 1))

    print(f"💾 寫入 SQLite：{DB_FILE} → 資料表：{TABLE_NAME}")
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)  # 重建，避免舊資料殘留混雜

    conn = sqlite3.connect(DB_FILE)
    df.to_sql(TABLE_NAME, conn, if_exists="replace", index=False)

    # 建立索引，加速依作物名稱 / 農藥名稱查詢（RAG 結構化查詢會用到）
    cur = conn.cursor()
    if "作物名稱" in df.columns:
        cur.execute(f'CREATE INDEX IF NOT EXISTS idx_crop ON {TABLE_NAME}("作物名稱")')
    if "農藥中文普通名稱" in df.columns:
        cur.execute(f'CREATE INDEX IF NOT EXISTS idx_pesticide ON {TABLE_NAME}("農藥中文普通名稱")')
    if "病蟲害名稱" in df.columns:
        cur.execute(f'CREATE INDEX IF NOT EXISTS idx_bug ON {TABLE_NAME}("病蟲害名稱")')
    conn.commit()

    # 驗證
    count = cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]
    crop_count = 0
    if "作物名稱" in df.columns:
        crop_count = cur.execute(f'SELECT COUNT(DISTINCT "作物名稱") FROM {TABLE_NAME}').fetchone()[0]

    conn.close()

    print(f"\n✅ 完成！")
    print(f"   資料表 {TABLE_NAME}：{count} 筆")
    print(f"   涵蓋作物種類：{crop_count} 種")
    print(f"   → {DB_FILE}")
    print("\n下一步：python build_index.py")


if __name__ == "__main__":
    main()