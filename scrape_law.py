# -*- coding: utf-8 -*-
"""
scrape_law.py — 爬取農藥相關法規全文，依條文切分
來源：農藥資訊服務網（跟農藥用量資料同一個網站，來源標註統一）

執行：python scrape_law.py
輸出：law_articles.csv（法規名稱、章節、條號、條文內容、來源網址、版本日期）
"""
import re
import requests
import urllib3
import pandas as pd
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 可依需要繼續擴充其他法規，格式：(法規名稱, 網址)
# 註：農藥標示管理辦法（id=121）內容為 PDF 附件，非網頁純文字，暫不納入此爬蟲
LAWS = [
    ("農藥管理法", "https://pesticide.aphia.gov.tw/information/Data/LawContent/110?code=1"),
    ("農藥管理規費收費標準", "https://pesticide.aphia.gov.tw/information/Data/LawContent/74?code=1"),
    ("農藥許可證申請及核發辦法", "https://pesticide.aphia.gov.tw/information/Data/LawContent/103?code=1"),
    ("農藥管理人員訓練及管理辦法", "https://pesticide.aphia.gov.tw/information/Data/LawContent/108?code=1"),
    ("農藥使用及農產品農藥殘留抽驗辦法", "https://pesticide.aphia.gov.tw/information/Data/LawContent/124?code=1"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://pesticide.aphia.gov.tw/information/",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
SESSION.verify = False


def scrape_one_law(law_name: str, url: str):
    print(f"📥 抓取《{law_name}》... {url}")
    r = SESSION.get(url, timeout=30)
    r.encoding = "utf-8"
    if r.status_code != 200:
        print(f"  ❌ 狀態碼 {r.status_code}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    full_text = soup.get_text(separator="\n")

    date_match = re.search(r"最後更新時間\s*\n?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", full_text)
    version_date = date_match.group(1) if date_match else "未知"

    # 只匹配「行首」的第 X 條（每條之間用 <br> 分隔，取代成換行），
    # 避免內文引用其他條號（如「依第十條規定辦理」「違反第十九條...」）被誤判成新條文
    pattern = re.compile(
        r"^[\s　]*第[\s　]*([０-９0-9一二三四五六七八九十百千]+(?:條之[一二三四五六七八九十]+|-[0-9]+|之[一二三四五六七八九十]+)?)[\s　]*條",
        re.MULTILINE,
    )
    chapter_pattern = re.compile(r"第[一二三四五六七八九十]+章[\s　]*([^\s\n]{1,20})")

    matches = list(pattern.finditer(full_text))
    if not matches:
        print("  ⚠️ 找不到條文標記，網站結構可能已變更")
        return []

    # 定位「第一條」在全文中的位置：法規本文必定從第一條開始，
    # 沿革區塊只會提到「後面」被修正過的條號（如第5-1、29、50條等），
    # 絕不會提到第一條本身。以此為錨點，捨棄它之前的所有假匹配（保險用，
    # 理論上行首錨定已足夠排除沿革，此為雙重防護）。
    first_article_pattern = re.compile(r"^[\s　]*第[\s　]*[一1][\s　]*條(?!之)", re.MULTILINE)
    anchor_m = first_article_pattern.search(full_text)
    body_start = anchor_m.start() if anchor_m else 0

    matches = [m for m in matches if m.start() >= body_start]

    rows = []
    current_chapter = ""
    for i, m in enumerate(matches):
        article_no = re.sub(r"[\s　]+", "", m.group(0))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        content = full_text[start:end].strip()
        content = re.sub(r"\n{2,}", "\n", content)
        content = re.sub(r"[ \t]+", "", content)

        preceding = full_text[max(0, m.start() - 50):m.start()]
        chap_m = chapter_pattern.search(preceding)
        if chap_m:
            current_chapter = chap_m.group(0)

        if not content or len(content) < 2:
            continue

        rows.append({
            "法規名稱": law_name,
            "章節": current_chapter,
            "條號": article_no,
            "條文內容": content,
            "版本日期": version_date,
            "來源網址": url,
        })

    print(f"  → 共擷取 {len(rows)} 條")
    return rows


def main():
    all_rows = []
    for name, url in LAWS:
        all_rows.extend(scrape_one_law(name, url))

    if not all_rows:
        print("❌ 沒有擷取到任何條文")
        return

    df = pd.DataFrame(all_rows)
    df.to_csv("law_articles.csv", index=False, encoding="utf-8-sig")
    print(f"\n✅ 完成！共 {len(df)} 條法規條文")
    print(f"   → law_articles.csv")
    print(df[["法規名稱", "章節", "條號"]].head(10).to_string())


if __name__ == "__main__":
    main()