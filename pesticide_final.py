# -*- coding: utf-8 -*-
"""農藥資訊服務網「病蟲害防治」用藥資料爬蟲。

2026/10 官網改版後的結構：
  - 作物清單改為內嵌在頁面中的 JSON（<script id="farmListData">），以 IsLeaf 標記末層作物
  - 用藥表格網址由 Query/BugFarmUserange 改為 Query/BugUserange?flag=0&farm=作物代碼
  - 表格欄位不變（另新增「作用機制」欄，如 IRAC: 1A）
舊版的 checkbox 解析保留為備援。
"""
import re
import json
import time
import requests
import urllib3
import pandas as pd
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL  = "https://pesticide.aphia.gov.tw/information"
BUG_PAGE  = f"{BASE_URL}/Query/Bug"
QUERY_URL = f"{BASE_URL}/Query/BugUserange"
DELAY = 1.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": BUG_PAGE,
}

SESSION = requests.Session()
SESSION.verify = False
SESSION.headers.update(HEADERS)


def get_farm_checkbox_list():
    print("Step 1: fetching farm list...")
    r = SESSION.get(BUG_PAGE, timeout=30)
    print(f"  status={r.status_code}, size={len(r.text)}")
    if r.status_code != 200:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    checkboxes = soup.find_all("input", attrs={"name": "farm", "type": "checkbox"})
    print(f"  farm checkboxes: {len(checkboxes)}")

    if not checkboxes:
        checkboxes = [
            cb for cb in soup.find_all("input", {"type": "checkbox"})
            if cb.get("value") and re.match(r"^[A-Za-z0-9]+$", cb["value"])
        ]

    items = []
    for cb in checkboxes:
        code = (cb.get("value") or "").strip()
        if not code:
            continue
        label_text = ""
        parent_label = cb.find_parent("label")
        if parent_label:
            label_text = parent_label.get_text(strip=True)
        if not label_text and cb.next_sibling:
            label_text = str(cb.next_sibling).strip()
        if not label_text:
            parent = cb.parent
            if parent:
                label_text = parent.get_text(strip=True)
        items.append((code, label_text))
    return items


def get_leaf_farms_from_json():
    """新版官網：從頁面內嵌的 farmListData JSON 取出所有末層作物。回傳 [{"代碼":..., "名稱":...}]"""
    print("Step 1: fetching farm list (JSON)...")
    r = SESSION.get(BUG_PAGE, timeout=30)
    r.encoding = "utf-8"
    print(f"  status={r.status_code}, size={len(r.text)}")
    if r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    tag = soup.find("script", id="farmListData")
    if not tag or not tag.string:
        print("  farmListData not found")
        return []
    tree = json.loads(tag.string)
    leaves, seen = [], set()

    def walk(nodes):
        for n in nodes or []:
            children = n.get("Children") or []
            if n.get("IsLeaf") or not children:
                code = (n.get("Farmid") or "").strip()
                if code and code not in seen:
                    seen.add(code)
                    leaves.append({"代碼": code, "名稱": (n.get("DisplayName") or "").strip()})
            walk(children)

    walk(tree)
    print(f"  leaf crops: {len(leaves)}")
    return leaves


def save_tree_csv(items):
    df = pd.DataFrame(items, columns=["代碼", "名稱"])
    df["碼數"] = df["代碼"].str.len()
    df.to_csv("farm_id_tree.csv", index=False, encoding="utf-8-sig")
    print(f"  saved farm_id_tree.csv ({len(df)} rows)")
    print(df["碼數"].value_counts().sort_index().to_string())
    return df


def get_leaf_farms(df):
    print("\nStep 2: filtering leaf nodes...")
    max_len = df["碼數"].max()
    leaves = df[df["碼數"] == max_len].drop_duplicates(subset="代碼")
    print(f"  max code length={max_len}, leaf count={len(leaves)}")
    return leaves.to_dict("records")


def parse_result_table(html, farm_code, crop_name):
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    col_map = {
        "病蟲名稱": "病蟲害名稱", "病蟲害名稱": "病蟲害名稱",
        "普通名稱": "農藥中文普通名稱", "含量": "農藥含量",
        "劑型代碼": "劑型", "劑型": "劑型",
        "每公頃每次用量": "每公頃每次用量", "稀釋倍數": "稀釋倍數",
        "使用時期": "使用時期", "使用時間": "使用時期",
        "施藥間隔": "施藥間隔", "施用次數": "施用次數",
        "安全採收期": "安全採收期_天", "施藥方法": "施用方法",
        "施用方法": "施用方法", "注意事項": "注意事項",
        "核准日期": "核准日期", "原始登記廠商名稱": "廠商名稱",
        "廠商名稱": "廠商名稱",
    }
    for table in soup.find_all("table"):
        header_row = table.find("tr")
        if not header_row:
            continue
        raw_headers = [th.get_text(separator=" ", strip=True) for th in header_row.find_all(["th", "td"])]
        headers = [col_map.get(h, h) for h in raw_headers]
        for tr in table.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if not tds:
                continue
            cells = [td.get_text(separator=" ", strip=True) for td in tds]
            row = {"作物代碼": farm_code, "作物名稱": crop_name}
            for i, h in enumerate(headers):
                if i < len(cells):
                    # 跳過表格自帶的「作物名稱」欄位，避免蓋掉我們查詢用的具體作物名
                    if h == "作物名稱":
                        row["登記分類名稱"] = cells[i]
                        continue
                    row[h] = cells[i]
            row["作物名稱"] = crop_name  # 再次強制覆蓋，保證不被任何欄位蓋掉
            rows.append(row)
    return rows


def crawl_all(leaf_farms):
    print(f"\nStep 3: crawling {len(leaf_farms)} crops...")
    all_rows = []
    total = len(leaf_farms)
    for i, item in enumerate(leaf_farms, 1):
        code = item["代碼"]
        name = item["名稱"] or code
        params = {"flag": "0", "farm": code}
        try:
            r = SESSION.get(QUERY_URL, params=params, timeout=20)
            r.encoding = "utf-8"
            if r.status_code == 200:
                rows = parse_result_table(r.text, code, name)
                print(f"  [{i}/{total}] {name} ({code}): {len(rows)} rows")
                all_rows.extend(rows)
            else:
                print(f"  [{i}/{total}] {name} ({code}): status {r.status_code}")
        except Exception as e:
            print(f"  [{i}/{total}] {name} ({code}): error {e}")
        time.sleep(DELAY)
    return all_rows


def get_leaf_farms_any():
    """優先使用新版 JSON 作物清單；失敗時退回舊版 checkbox 解析。"""
    leaves = get_leaf_farms_from_json()
    if leaves:
        return leaves
    items = get_farm_checkbox_list()
    if not items:
        return []
    return get_leaf_farms(save_tree_csv(items))


def main():
    leaf_farms = get_leaf_farms_any()
    if not leaf_farms:
        print("no leaf farms found")
        return
    all_rows = crawl_all(leaf_farms)

    print("\nStep 4: saving...")
    if all_rows:
        df_out = pd.DataFrame(all_rows).drop_duplicates()
        df_out.to_csv("all_pesticide_leaf.csv", index=False, encoding="utf-8-sig")
        print(f"DONE. {len(df_out)} rows, {df_out['作物名稱'].nunique()} crops")
        print(df_out["作物名稱"].value_counts().head(15).to_string())
    else:
        print("no rows collected")


if __name__ == "__main__":
    main()