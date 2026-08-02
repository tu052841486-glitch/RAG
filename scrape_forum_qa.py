# -*- coding: utf-8 -*-
"""
scrape_forum_qa.py — 爬取農業藥物試驗所「線上諮詢問答系統」全部真實問答
來源：https://mbox.acri.gov.tw/TA02.asp（清單，共 34 頁）
      https://mbox.acri.gov.tw/TA03.asp?Enc=xxx（每筆詳情頁）

⚠️ 重點：此網站為 Big5 編碼（非 UTF-8），程式已處理編碼轉換。

執行：python scrape_forum_qa.py
輸出：forum_qa.csv（案號、類別、日期、問題、答案、來源網址）
"""
import re
import time
import requests
import urllib3
import pandas as pd
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://mbox.acri.gov.tw"
LIST_URL = f"{BASE}/TA02.asp"
DETAIL_URL = f"{BASE}/TA03.asp"
TOTAL_PAGES = 34
DELAY = 0.8

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
SESSION.verify = False


def fetch_big5(url, params=None):
    """抓取頁面並用 Big5 正確解碼（此網站不是 UTF-8）"""
    r = SESSION.get(url, params=params, timeout=30)
    r.encoding = "big5"  # 關鍵：強制指定編碼，否則會亂碼
    return r.text


def get_list_page(page_no: int):
    """抓第 page_no 頁的問題清單，回傳 [(案號, 類別, 日期, 問題, enc), ...]"""
    params = {
        "Case_Type": "", "IsClick": "", "KeySrh": "", "Sch": "",
        "QNA_No": "", "FormGRD_Page": page_no,
    }
    html = fetch_big5(LIST_URL, params=params)
    soup = BeautifulSoup(html, "html.parser")

    rows = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        case_no = tds[0].get_text(strip=True)
        if not re.match(r"^\d{5,}$", case_no):
            continue  # 略過表頭或非資料列

        category = tds[1].get_text(strip=True)
        date_raw = tds[2].get_text(strip=True)

        link = tds[3].find("a")
        if not link or not link.get("href"):
            continue
        question = link.get_text(strip=True)
        enc_match = re.search(r"Enc=([a-f0-9]+)", link["href"])
        if not enc_match:
            continue
        enc = enc_match.group(1)

        rows.append({
            "案號": case_no,
            "類別": category,
            "日期": date_raw,
            "問題": question,
            "enc": enc,
        })
    return rows


def get_detail_answer(enc: str):
    """抓詳情頁，回傳 (完整問題, 答案)"""
    params = {"Enc": enc, "Click": "Y", "KeySrh": "", "Case_Type": "", "Sch": "", "FormGRD_Page": 1}
    html = fetch_big5(DETAIL_URL, params=params)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")

    q_match = re.search(r"主\s*題\s*(.*?)(?=\n-?\s*回\s*答|\n-?\s*回\s*覆)", text, re.DOTALL)
    a_match = re.search(r"(?:回\s*答|回\s*覆)\s*(.*?)(?=\n-?\s*附件|\n\[|\Z)", text, re.DOTALL)

    question_full = q_match.group(1).strip() if q_match else ""
    answer = a_match.group(1).strip() if a_match else ""
    answer = re.sub(r"\n{2,}", "\n", answer)
    return question_full, answer


def main():
    all_rows = []
    print(f"📋 開始抓取問題清單（共 {TOTAL_PAGES} 頁）...")
    for page in range(1, TOTAL_PAGES + 1):
        try:
            rows = get_list_page(page)
            print(f"  第 {page}/{TOTAL_PAGES} 頁：{len(rows)} 筆")
            all_rows.extend(rows)
        except Exception as e:
            print(f"  第 {page} 頁失敗：{e}")
        time.sleep(DELAY)

    print(f"\n✅ 清單抓取完成，共 {len(all_rows)} 筆問題")
    print(f"📥 開始逐筆抓取詳細答案...")

    final_rows = []
    total = len(all_rows)
    for i, item in enumerate(all_rows, 1):
        try:
            q_full, answer = get_detail_answer(item["enc"])
            final_rows.append({
                "案號": item["案號"],
                "類別": item["類別"],
                "日期": item["日期"],
                "問題": q_full or item["問題"],
                "答案": answer,
                "來源網址": f"{DETAIL_URL}?Enc={item['enc']}",
            })
            if i % 20 == 0 or i == total:
                print(f"  [{i}/{total}] 完成")
        except Exception as e:
            print(f"  [{i}/{total}] 案號 {item['案號']} 失敗：{e}")
        time.sleep(DELAY)

    df = pd.DataFrame(final_rows)
    df.to_csv("forum_qa.csv", index=False, encoding="utf-8-sig")
    print(f"\n✅ 完成！共 {len(df)} 筆真實問答")
    print(f"   → forum_qa.csv")
    print("\n各類別筆數：")
    print(df["類別"].value_counts().to_string())


if __name__ == "__main__":
    main()
