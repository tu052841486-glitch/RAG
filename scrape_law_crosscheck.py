# -*- coding: utf-8 -*-
"""
scrape_law_crosscheck.py — 從第二個獨立官方來源重新爬取同一批法規，
與主要來源（law_articles.csv，來自 pesticide.aphia.gov.tw）比對條文是否一致。

用途：僅供「報告佐證」使用，證明法規內容經過多來源交叉驗證，
      不會把這份資料塞進 RAG 向量庫（避免重複內容稀釋檢索精準度）。

次要來源：農業部主管法規共用系統 https://law.moa.gov.tw
（第一來源 pesticide.aphia.gov.tw 已在 scrape_law.py 完成）

執行順序：
  1. python scrape_law.py            → 產生 law_articles.csv（主要，餵給 RAG）
  2. python scrape_law_crosscheck.py → 產生 law_crosscheck_report.csv（比對報告）

輸出：law_crosscheck_report.csv（每條法條在兩來源的比對結果：一致 / 不一致 / 其中一方缺漏）
"""
import re
import os
import unicodedata
import difflib
import requests
import urllib3
import pandas as pd
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PRIMARY_CSV = "law_articles.csv"

# 次要來源網址對照（法規名稱, moa.gov.tw 網址）
# 註：「農藥管理規費收費標準」在 moa.gov.tw 查無獨立頁面，故不納入交叉比對，
#     僅以單一來源（aphia.gov.tw）收錄，報告中應如實註明。
SECONDARY_LAWS = {
    "農藥管理法": "https://law.moa.gov.tw/LawContent.aspx?id=FL014387",
    "農藥許可證申請及核發辦法": "https://law.moa.gov.tw/LawContent.aspx?id=FL027761",
    "農藥管理人員訓練及管理辦法": "https://law.moa.gov.tw/LawContent.aspx?id=FL045708",
    "農藥使用及農產品農藥殘留抽驗辦法": "https://law.moa.gov.tw/LawContent.aspx?id=FL014395",
}

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


CN_NUM = {"零":0,"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}

def cn_to_arabic(cn: str) -> str:
    """把中文數字（十以內含十位數，如「十九」「五十九」）轉成阿拉伯數字字串"""
    if cn.isdigit():
        return cn
    if not cn:
        return cn
    if "十" not in cn:
        return "".join(str(CN_NUM.get(c, c)) for c in cn) if all(c in CN_NUM for c in cn) else cn
    parts = cn.split("十")
    tens = CN_NUM.get(parts[0], 1) if parts[0] else 1
    ones = CN_NUM.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
    return str(tens * 10 + ones)


def normalize_article_no(article_no: str) -> str:
    """統一條號格式：把「第X條」「第X-1條」中的 X（不論中文或阿拉伯數字）轉成統一的阿拉伯數字格式，
    確保不同來源網站（一個用中文數字、一個用阿拉伯數字）也能正確比對"""
    m = re.match(r"第([0-9一二三四五六七八九十百千]+)(-[0-9]+)?條", article_no)
    if not m:
        return article_no
    num_part = m.group(1)
    suffix = m.group(2) or ""
    arabic = cn_to_arabic(num_part)
    return f"第{arabic}條{suffix}"


def fetch_secondary_articles(law_name: str, url: str):
    """從 law.moa.gov.tw 抓條文，回傳 {條號(已正規化): 條文內容} 字典"""
    r = SESSION.get(url, timeout=30)
    r.encoding = "utf-8"
    if r.status_code != 200:
        print(f"  ❌ {law_name} 次要來源狀態碼 {r.status_code}")
        return {}

    soup = BeautifulSoup(r.text, "html.parser")
    full_text = unicodedata.normalize("NFKC", soup.get_text(separator="\n"))

    # 只匹配「行首」的第 X 條，避免內文引用其他條號（如「依第十條規定辦理」）被誤判成新條文
    pattern = re.compile(
        r"^[\s　]*第[\s　]*([0-9一二三四五六七八九十百千]+(?:-[0-9]+)?)[\s　]*條",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(full_text))

    articles = {}
    for i, m in enumerate(matches):
        article_no = normalize_article_no(re.sub(r"[\s　]+", "", m.group(0)))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        content = full_text[start:end].strip()
        content = re.sub(r"\n{2,}", "\n", content)
        content = re.sub(r"[ \t]+", "", content)
        if content:
            articles[article_no] = content
    return articles


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def main():
    if not os.path.exists(PRIMARY_CSV):
        print(f"❌ 找不到 {PRIMARY_CSV}，請先執行 scrape_law.py")
        return

    primary_df = pd.read_csv(PRIMARY_CSV, encoding="utf-8-sig", dtype=str).fillna("")
    print(f"📋 主要來源（pesticide.aphia.gov.tw）共 {len(primary_df)} 條，開始交叉比對...\n")

    results = []
    for law_name, url in SECONDARY_LAWS.items():
        print(f"🔍 比對《{law_name}》...")
        secondary_articles = fetch_secondary_articles(law_name, url)
        primary_subset = primary_df[primary_df["法規名稱"] == law_name]

        if primary_subset.empty:
            print(f"  ⚠️ 主要來源沒有這份法規，略過")
            continue

        matched, mismatched, missing_in_secondary = 0, 0, 0
        for _, row in primary_subset.iterrows():
            article_no = normalize_article_no(row["條號"])
            primary_text = unicodedata.normalize("NFKC", row["條文內容"])
            secondary_text = secondary_articles.get(article_no, "")

            if not secondary_text:
                status = "次要來源缺漏"
                missing_in_secondary += 1
                sim = 0.0
            else:
                sim = similarity(primary_text, secondary_text)
                if sim > 0.9:
                    status = "一致"
                    matched += 1
                else:
                    status = "內容有差異"
                    mismatched += 1

            results.append({
                "法規名稱": law_name,
                "條號": article_no,
                "相似度": round(sim, 3),
                "比對結果": status,
                "次要來源網址": url,
            })

        total = matched + mismatched + missing_in_secondary
        print(f"  → 一致 {matched}／不一致 {mismatched}／缺漏 {missing_in_secondary}（共 {total} 條）\n")

    if results:
        df_out = pd.DataFrame(results)
        df_out.to_csv("law_crosscheck_report.csv", index=False, encoding="utf-8-sig")
        print(f"✅ 完成！比對報告 → law_crosscheck_report.csv")
        print(f"\n總覽：")
        print(df_out["比對結果"].value_counts().to_string())
    else:
        print("❌ 沒有產生任何比對結果")


if __name__ == "__main__":
    main()