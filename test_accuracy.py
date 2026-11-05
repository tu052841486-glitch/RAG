"""線上系統正確率測試：逐題呼叫 /api/ask，檢查回答內容、來源與拒答是否符合預期。
用法：python test_accuracy.py            （測線上）
      python test_accuracy.py local      （測本機 localhost:8000）
結果印在畫面上，並寫入 accuracy_report.txt
"""
import json, os, sys, time, urllib.request
from datetime import datetime

BASE = "http://localhost:8000" if "local" in sys.argv[1:] else "https://pesticide-doctor-api.fly.dev"

# (類別, 題目, 是否應拒答, 回答須含其一, 來源標題須含其一, 是否需要處方卡[, 回答不可出現的字[, 先前對話]])
CASES = [
    ("農民", "我的芒果得了炭疽病，葉子和果實上都有黑色病斑，請問可以用哪些農藥防治？安全採收期是幾天？", False, ["炭疽病"], ["炭疽病"], True),
    # 登記資料若有每公頃用量就應算出總量，沒有就應給每桶加藥量；兩種情況都不可自行「假設」數字
    ("農民", "芒果炭疽病用三氟敏，我有 0.2 公頃的果園，請問要用多少藥？", False, ["0.06", "一桶", "每桶", "16 公升", "16公升"], [], False,
     ["假設每公頃", "假設你每公頃", "每公頃需要", "每公頃大約", "每公頃用水量是", "每公頃噴", "1000 公升", "1,000 公升"]),
    ("農民", "高麗菜小菜蛾要用什麼藥？", False, ["小菜蛾"], ["小菜蛾"], True),
    ("農民", "空心菜可以噴加保扶嗎？", False, ["空心菜"], [], False),
    ("安全", "農藥中毒要怎麼急救？", False, ["就醫", "送醫", "毒藥物", "急救"], ["中毒"], False),
    ("安全", "噴藥沒戴口罩頭暈", False, ["中毒", "就醫", "送醫", "防護"], [], False),
    ("法規", "農藥販賣業者許可執照的有效期限是多久？", False, ["五年", "5年", "5 年"], ["農藥管理法"], False),
    ("法規", "我要開農藥行", False, ["販賣業", "執照"], ["農藥管理法"], False),
    ("法規", "用無人機噴農藥有什麼規定？", False, ["識別碼", "風速", "公尺"], ["殘留抽驗辦法"], False),
    ("俗名", "草莓有紅蜘蛛怎麼辦？", False, ["葉蟎"], ["葉蟎"], True),
    ("俗名", "高麗菜長吊絲蟲", False, ["小菜蛾"], ["小菜蛾"], True),
    ("安全", "不小心喝到除草劑嘉磷塞怎麼辦？", False, ["催吐"], ["臺北榮總", "長庚"], False),
    ("安全", "農藥噴到眼睛怎麼辦？", False, ["沖洗"], ["臺北榮總", "長庚"], False),
    ("友善", "番茄明天要採收，有沒有不用農藥的方法？", False, ["免登記", "資材"], ["農業部"], False, ["公克", "毫升", "每公升"]),
    ("買藥", "嘉義縣民雄鄉哪裡可以買農藥？", False, ["家"], ["公司登記"], False),
    # 接在用藥對話之後問「哪裡買」，必須仍走販賣業者查詢（不可被前文帶偏）
    ("買藥", "嘉義市西區哪裡可以買農藥？", False, ["家"], ["公司登記"], False, [],
     [{"role": "user", "text": "高麗菜小菜蛾要用什麼藥？"}, {"role": "bot", "text": "高麗菜小菜蛾可以用培丹、大利松等登記藥劑。"}]),
    ("邊界", "今天天氣如何呢？", True, [], [], False),
    ("邊界", "幫我寫一個 Python 程式", True, [], [], False),
]


def ask(q, history=None):
    req = urllib.request.Request(f"{BASE}/api/ask", data=json.dumps({"question": q, "history": history or []}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def check(case, res):
    _, q, refuse, must, src_must, need_cards = case[:6]
    must_not = case[6] if len(case) > 6 else []
    answer = res.get("answer", "")
    sources = " ".join(s.get("title", "") for s in res.get("sources") or [])
    refused = bool(res.get("isRefusal")) or answer.startswith("知識庫無此資訊")
    problems = []
    if refuse != refused:
        problems.append("應拒答卻回答了" if refuse else "不應拒答卻拒答了")
    if not refuse:
        if must and not any(w in answer for w in must):
            problems.append(f"回答未提到：{'／'.join(must)}")
        if src_must and not any(w in sources for w in src_must):
            problems.append(f"來源未包含：{'／'.join(src_must)}")
        if need_cards and not res.get("cards"):
            problems.append("沒有處方卡")
        bad = [w for w in must_not if w in answer]
        if bad:
            problems.append(f"回答出現不該有的字：{'／'.join(bad)}（AI 自行假設每公頃用水量）")
    return problems


lines = [f"農藥博士 正確率測試　{datetime.now():%Y-%m-%d %H:%M}　對象：{BASE}", ""]
passed = 0
for i, case in enumerate(CASES, 1):
    t = time.time()
    try:
        res = ask(case[1], case[7] if len(case) > 7 else None)
        problems = check(case, res)
    except Exception as e:
        res, problems = {}, [f"呼叫失敗：{e}"]
    ok = not problems
    passed += ok
    sec = time.time() - t
    head = f"{'✅' if ok else '❌'} {i:>2}. [{case[0]}] {case[1]}（{sec:.1f} 秒）"
    print(head)
    lines.append(head)
    for p in problems:
        print(f"      ⚠️ {p}")
        lines.append(f"      ⚠️ {p}")
    lines.append(f"      回答：{(res.get('answer') or '').replace(chr(10), ' ')[:160]}")
    lines.append(f"      來源：{'、'.join(s.get('title', '') for s in (res.get('sources') or [])[:4])}")
    lines.append("")

summary = f"通過 {passed} / {len(CASES)} 題，正確率 {passed / len(CASES):.0%}"
print("\n" + summary)
lines.insert(1, summary)
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "accuracy_report.txt")
open(path, "w", encoding="utf-8").write("\n".join(lines))
print(f"詳細結果：{path}")
