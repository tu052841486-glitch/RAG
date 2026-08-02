# -*- coding: utf-8 -*-
"""
scrape_residue.py — 爬取《農藥殘留容許量標準》附表一，解析成結構化資料
來源：高雄市政府衛生局（法規本文）+ 附表 PDF
      https://orgws.kcg.gov.tw/001/KcgOrgUploadFiles/463/relfile/77793/347247/7ca0f288-f7ad-4826-8a3f-20f0c6a2fa82.pdf

⚠️ 重要提醒：
  這份 PDF 規模龐大（數千筆農藥-作物-限量組合），且因 PDF 排版換行，
  部分中文詞彙會被拆成多行（如「十字花科小葉菜 類」），本解析器採用
  「盡力而為」策略：先重組被拆開的行，再用正規表達式抓出「數值+分類」
  作為每筆資料的結尾錨點，往前推算作物名稱與農藥名稱。

  這不保證 100% 準確，建議跑完後抽查 residue_limits.csv 內容，
  確認資料格式是否正確，如有明顯錯亂可以再回報調整。

執行：
  pip install pdfplumber --break-system-packages
  python scrape_residue.py

輸出：residue_limits.csv（農藥英文名、農藥中文名、作物/分類、限量ppm、用途分類）
"""
import re
import unicodedata
import requests
import urllib3
import pandas as pd

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PDF_URL = "https://orgws.kcg.gov.tw/001/KcgOrgUploadFiles/463/relfile/77793/347247/7ca0f288-f7ad-4826-8a3f-20f0c6a2fa82.pdf"
PDF_FILE = "residue_standard.pdf"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

CATEGORIES = ["殺蟲劑", "殺菌劑", "除草劑", "生長調節劑", "殺蟎劑", "殺線蟲劑"]


def download_pdf():
    print(f"📥 下載 PDF...")
    r = requests.get(PDF_URL, headers=HEADERS, verify=False, timeout=60)
    with open(PDF_FILE, "wb") as f:
        f.write(r.content)
    print(f"  → 已存 {PDF_FILE}（{len(r.content)/1024/1024:.1f} MB）")


def extract_text():
    import pdfplumber
    print("📄 解析 PDF 文字內容（頁數較多，可能需要幾分鐘）...")
    all_text = []
    with pdfplumber.open(PDF_FILE) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            all_text.append(text)
            if i % 20 == 0 or i == total:
                print(f"  頁 {i}/{total}")
    return "\n".join(all_text)


def parse_rows(full_text: str):
    full_text = unicodedata.normalize("NFKC", full_text)

    # 只處理附表一內容本體（到附表二之前）
    start_idx = full_text.find("2,4-D")
    if start_idx == -1:
        start_idx = 0
    # 注意：「附表二」這幾個字在前面第三條法規條文裡就已出現過一次
    # （「...詳如附表一及附表二」），必須從 start_idx 之後才開始找，
    # 否則會找到太早的位置，導致切出空範圍
    end_idx = full_text.find("附表二", start_idx)
    if end_idx == -1:
        end_idx = len(full_text)
    text = full_text[start_idx:end_idx]

    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # 每筆資料的行尾一定是「數值(可能含*) + 分類」；但當作物名稱過長換行時，
    # 換行後的殘留文字（如單獨一行的「類」，或英文農藥名稱的接續字，
    # 或括號附註如「(芹菜除外)」）是屬於「前一筆」資料的延續，不是下一筆的開頭。
    row_end_pattern = re.compile(
        r"^(.*?)\s+([\d]+(?:\.\d+)?\*?)\s+(殺蟲劑|殺菌劑|除草劑|生長調節劑|殺蟎劑|殺線蟲劑|燻蒸劑|殺螺劑)\s*$"
    )
    skip_prefixes = ("附表", "註", "類別", "農作物")

    rows = []
    for line in lines:
        if line.startswith(skip_prefixes):
            continue
        m = row_end_pattern.match(line)
        if m:
            prefix, value, category = m.groups()
            rows.append({
                "農藥名稱與作物": prefix.strip(),
                "限量_ppm": value.rstrip("*"),
                "備註": "外源性/國際暫訂標準（依定量極限訂定）" if "*" in value else "",
                "用途分類": category,
            })
        else:
            # 這是上一筆資料的換行延續內容，接續補上去（還原完整文字）
            if rows:
                rows[-1]["農藥名稱與作物"] += " " + line

    for r in rows:
        r["農藥名稱與作物"] = r["農藥名稱與作物"].strip()

    if not rows:
        print("\n🔍 診斷：找不到任何配對，以下是前 15 行實際處理過的文字內容：")
        for l in lines[:15]:
            print(f"  [{len(l)}字] {l!r}")

    return rows


def main():
    download_pdf()
    full_text = extract_text()

    # 先存一份完全未經處理的原始文字，方便診斷實際換行結構
    with open("residue_raw_text.txt", "w", encoding="utf-8") as f:
        f.write(full_text)
    print(f"📝 已存原始文字 → residue_raw_text.txt（未經任何規則處理，供診斷用）")

    rows = parse_rows(full_text)

    if not rows:
        print("❌ 沒有解析到任何資料，PDF 結構可能與預期不同")
        return

    df = pd.DataFrame(rows)
    df.to_csv("residue_limits.csv", index=False, encoding="utf-8-sig")
    print(f"\n✅ 完成（盡力而為版本）！共解析 {len(df)} 筆資料")
    print(f"   → residue_limits.csv")
    print(f"\n⚠️ 請務必打開 residue_limits.csv 抽查幾筆，確認「中文名稱與作物」")
    print(f"   欄位有沒有明顯錯亂（因為中英文/作物名稱沒有完全拆開）")
    print("\n預覽前 10 筆：")
    print(df.head(10).to_string())


if __name__ == "__main__":
    main()