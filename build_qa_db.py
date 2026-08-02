# -*- coding: utf-8 -*-
"""
build_qa_db.py — 把兩份問答資料合併匯入 SQLite（qa_knowledge 資料表）
  1. qa_pairs.csv   （106題官方標準問答集，來自 build_qa_pairs.py）
  2. forum_qa.csv   （農業藥物試驗所線上諮詢真實問答，來自 scrape_forum_qa.py）

執行：python build_qa_db.py
兩份資料缺一份也可正常執行（會提示略過），方便分階段完成。
沿用同一個 pesticides.db，不影響 pesticides / regulations 資料表
"""
import sqlite3
import pandas as pd
import os

DB_FILE = "pesticides.db"
TABLE_NAME = "qa_knowledge"


def load_official_qa():
    if not os.path.exists("qa_pairs.csv"):
        print("  ⚠️ 找不到 qa_pairs.csv，略過官方標準問答集（未影響其餘流程）")
        return pd.DataFrame()
    df = pd.read_csv("qa_pairs.csv", encoding="utf-8-sig", dtype=str).fillna("")
    df = df.rename(columns={"章節": "分類"})
    df["來源類別"] = "官方標準問答集"
    df["來源網址"] = "https://www.acri.gov.tw/Uploads/Item/76022b9d-3394-4aea-8683-c39835555ea5.pdf"
    return df[["分類", "問題", "答案", "來源類別", "來源網址"]]


def load_forum_qa():
    if not os.path.exists("forum_qa.csv"):
        print("  ⚠️ 找不到 forum_qa.csv，略過線上諮詢真實問答（未影響其餘流程）")
        return pd.DataFrame()
    df = pd.read_csv("forum_qa.csv", encoding="utf-8-sig", dtype=str).fillna("")
    df = df.rename(columns={"類別": "分類"})
    df["來源類別"] = "線上諮詢真實問答"
    return df[["分類", "問題", "答案", "來源類別", "來源網址"]]


def load_poison_qa():
    if not os.path.exists("poison_qa.csv"):
        print("  ⚠️ 找不到 poison_qa.csv，略過中毒急救資訊（未影響其餘流程）")
        return pd.DataFrame()
    df = pd.read_csv("poison_qa.csv", encoding="utf-8-sig", dtype=str).fillna("")
    df["來源類別"] = "中毒急救資訊（台北榮總）"
    return df[["分類", "問題", "答案", "來源類別", "來源網址"]]


def main():
    if not os.path.exists(DB_FILE):
        print(f"❌ 找不到 {DB_FILE}，請先執行過 build_db.py 建立主資料庫")
        return

    df_official = load_official_qa()
    df_forum = load_forum_qa()
    df_poison = load_poison_qa()

    if df_official.empty and df_forum.empty and df_poison.empty:
        print("❌ 三份問答資料都不存在，請先執行 build_qa_pairs.py / scrape_forum_qa.py / build_poison_qa.py")
        return

    df = pd.concat([df_official, df_forum, df_poison], ignore_index=True)
    df = df[df["答案"].str.len() > 0]  # 過濾掉答案為空的無效資料
    df.insert(0, "id", range(1, len(df) + 1))

    conn = sqlite3.connect(DB_FILE)
    df.to_sql(TABLE_NAME, conn, if_exists="replace", index=False)

    cur = conn.cursor()
    cur.execute(f'CREATE INDEX IF NOT EXISTS idx_qa_category ON {TABLE_NAME}("來源類別")')
    conn.commit()
    count = cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]
    conn.close()

    print(f"\n✅ 完成！{TABLE_NAME} 資料表：共 {count} 組問答")
    print(f"   官方標準問答集：{len(df_official)} 組")
    print(f"   線上諮詢真實問答：{len(df_forum)} 組")
    print(f"   中毒急救資訊：{len(df_poison)} 組")
    print("下一步：python build_qa_index.py")


if __name__ == "__main__":
    main()

