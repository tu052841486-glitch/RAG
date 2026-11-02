# -*- coding: utf-8 -*-
"""
scrape_law.py — 爬取農藥相關法規全文，依條文切分
來源：農藥資訊服務網（跟農藥用量資料同一個網站，來源標註統一）

執行：python scrape_law.py
輸出：law_articles.csv（法規名稱、章節、條號、條文內容、來源網址、版本日期）
"""
import re
import html
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
    # 農藥資訊服務網的本辦法頁面（LawContent/124）改版後「內容」區塊為空，只剩附件連結，
    # 且該頁版本停留在 112 年；改從法務部全國法規資料庫擷取（已含 115/07/31 修正）
    ("農藥使用及農產品農藥殘留抽驗辦法", "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=M0140018"),
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

# 條號：只匹配「行首」的第 X 條（每條之間用 <br> 分隔，取代成換行），
# 避免內文引用其他條號（如「依第十條規定辦理」「違反第十九條...」）被誤判成新條文。
# 支援三種寫法：「第　六　條」「第　六　條之一」（「之一」在「條」後面）、「第 5-1 條」。
# 修正前「之一」寫在「條」之前，導致「第六條之一」被切成「第六條」，
# 與原本的第六條重複，且條文內容開頭殘留「之一」。
ARTICLE_PATTERN = re.compile(
    r"^[\s　]*第[\s　]*[０-９0-9一二三四五六七八九十百千零]+(?:-[0-9]+)?[\s　]*條"
    r"(?:[\s　]*之[\s　]*[一二三四五六七八九十]+)?",
    re.MULTILINE,
)
CHAPTER_PATTERN = re.compile(r"第[一二三四五六七八九十]+章[\s　]*([^\s\n]{1,20})")
FIRST_ARTICLE_PATTERN = re.compile(r"^[\s　]*第[\s　]*[一1][\s　]*條(?![\s　]*之)", re.MULTILINE)


_CN_DIGITS = "零一二三四五六七八九"


def _to_chinese_number(n: int) -> str:
    """1～999 的阿拉伯數字轉國字（法規條號用，例：53 → 五十三、101 → 一百零一）。"""
    if n < 10:
        return _CN_DIGITS[n]
    if n < 20:
        return "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    if n < 100:
        return _CN_DIGITS[n // 10] + "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    rest = n % 100
    tail = "" if rest == 0 else ("零" + _to_chinese_number(rest) if rest < 10 else
                                ("一" + _to_chinese_number(rest) if rest < 20 else _to_chinese_number(rest)))
    return _CN_DIGITS[n // 100] + "百" + tail


def normalize_article_no(raw: str) -> str:
    """統一條號格式：「第6-1條」「第 6 條之 1」→「第六條之一」，國字條號維持不變。"""
    t = re.sub(r"[\s　]+", "", raw).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    m = re.match(r"第(\d+)(?:-(\d+))?條(?:之(\d+|[一二三四五六七八九十]+))?$", t)
    if not m:
        return t
    main, sub1, sub2 = m.group(1), m.group(2), m.group(3)
    out = f"第{_to_chinese_number(int(main))}條"
    sub = sub1 or sub2
    if sub:
        out += "之" + (_to_chinese_number(int(sub)) if sub.isdigit() else sub)
    return out


def scrape_one_law(law_name: str, url: str):
    print(f"📥 抓取《{law_name}》... {url}")
    r = SESSION.get(url, timeout=30)
    r.encoding = "utf-8"
    if r.status_code != 200:
        print(f"  ❌ 狀態碼 {r.status_code}")
        return []

    # 2026/10 官網改版：條文內容以「HTML 跳脫字元」嵌在頁面中（&lt;p&gt;第　一　條…），
    # 需先還原（unescape）再解析；舊版頁面還原後內容不變，兩者皆相容。
    soup = BeautifulSoup(html.unescape(r.text), "html.parser")
    full_text = soup.get_text(separator="\n")

    date_match = re.search(r"最後更新時間[^0-9]{0,20}([0-9]{4}-[0-9]{2}-[0-9]{2})", full_text)
    roc_match = re.search(r"修正日期[：:\s]*民國[\s　]*(\d+)[\s　]*年[\s　]*(\d+)[\s　]*月[\s　]*(\d+)[\s　]*日", full_text)
    if date_match:
        version_date = date_match.group(1)
    elif roc_match:  # 全國法規資料庫：民國年 → 西元年
        y, mo, d = (int(x) for x in roc_match.groups())
        version_date = f"{y + 1911}-{mo:02d}-{d:02d}"
    else:
        version_date = "未知"

    matches = list(ARTICLE_PATTERN.finditer(full_text))
    if not matches:
        print("  ⚠️ 找不到條文標記，網站結構可能已變更")
        return []

    # 定位「第一條」在全文中的位置：法規本文必定從第一條開始，
    # 沿革區塊只會提到「後面」被修正過的條號（如第5-1、29、50條等），
    # 絕不會提到第一條本身。以此為錨點，捨棄它之前的所有假匹配。
    anchor_m = FIRST_ARTICLE_PATTERN.search(full_text)
    body_start = anchor_m.start() if anchor_m else 0
    matches = [m for m in matches if m.start() >= body_start]

    rows = []
    seen_numbers = set()
    current_chapter = ""
    for i, m in enumerate(matches):
        article_no = normalize_article_no(m.group(0))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        content = full_text[start:end].strip()
        # 最後一條之後常接著頁尾（更新時間、附件、版權宣告…），遇到頁尾關鍵字就截斷
        for marker in ("最後更新時間", "回上一頁", "相關附件", "附件下載", "瀏覽人次", "上一筆", "下一筆",
                       "版權所有", "Copyright", "農業部動植物防疫檢疫署", ":::", "訂閱電子報", "網站資訊區塊"):
            cut = content.find(marker)
            if cut > 0:
                content = content[:cut].strip()
        # 附件檔名（如「附件農作物農藥殘留限量表.pdf」）與「相關連結」區塊屬於頁面元素，不是條文
        m_att = re.search(r"附件[^。\n]{0,60}?\.(?:pdf|docx?|odt|xlsx?)", content, re.I)
        if m_att and m_att.start() > 0:
            content = content[:m_att.start()].strip()
        cut = content.find("相關連結")
        if cut > 0:
            content = content[:cut].strip()
        # 條文以跳脫字元嵌在 HTML 屬性中，還原後最後一條結尾會殘留屬性的引號與角括號（如「">」）
        content = re.sub(r'["\'＂]?\s*/?>\s*$', "", content).strip()
        content = content.replace("本條文有附件", "").strip()
        content = re.sub(r"\n{2,}", "\n", content)
        # 網頁條文常夾雜全形空白（\u3000）、不斷行空白（\xa0）與零寬字元，
        # 會把「燻蒸」拆成「燻 蒸」而影響檢索，一併清除（中文條文不需要空白，保留換行分段）
        content = re.sub(r"[ \t\u3000\xa0\u200b]+", "", content)

        preceding = full_text[max(0, m.start() - 50):m.start()]
        chap_m = CHAPTER_PATTERN.search(preceding)
        if chap_m:
            current_chapter = chap_m.group(0)

        if not content or len(content) < 2:
            continue
        # 同一部法規不應出現重複條號；若出現，代表網頁結構異常，保留第一次出現的版本
        if article_no in seen_numbers:
            print(f"  ⚠️ 條號重複，已略過：{article_no}")
            continue
        seen_numbers.add(article_no)

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
