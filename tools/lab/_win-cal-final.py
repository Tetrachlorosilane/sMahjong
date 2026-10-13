# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
import json, glob, collections
d = r"S:\mahjong-training\raw\v4-wl-win-cal-c1-g01"
files = sorted(glob.glob(d + r"\g*.jsonl"))
def student_seats(f):
    for ln in open(f, encoding="utf-8", errors="replace"):
        r=json.loads(ln)
        if r.get("type")=="game":
            return {i:str(p) for i,p in enumerate(r.get("policies") or []) if "@" in str(p)}
    return {}
hands=0; rows=0
act_student=0; paid_student=0
act_rows_any=0
viol=[]; wh_bad=0
for f in files:
    ss=student_seats(f)
    byhand=collections.OrderedDict()
    for ln in open(f, encoding="utf-8", errors="replace"):
        r=json.loads(ln)
        if r.get("type")!="decision": continue
        rows+=1
        seat=int(r["seat"]); ch=r.get("chosen") or ""
        k=(r["game"], r["hand_no"])
        st=byhand.setdefault(k, {"rows":[], "agari":None, "winner":None})
        st["rows"].append((seat,ch))
        st["agari"]=bool(r.get("hand_agari")); st["winner"]=int(r.get("hand_winner",-1))
    for k,st in byhand.items():
        hands+=1
        scoped=[(s,c) for (s,c) in st["rows"] if s in ss]
        hits=[(s,c) for (s,c) in scoped if c in ("tsumo","ron")]
        act_rows_any += sum(1 for (s,c) in st["rows"] if c in ("tsumo","ron"))
        act_student += len(hits)
        hx = bool(st["agari"]) and st["winner"] in ss
        # 行级付奖：该行 chosen 是 win 动作 且 手级记录 winner == 该席（= 真正拿了和了点的那家）
        paid = [(s,c) for (s,c) in hits if st["winner"]==s]
        paid_student += len(paid)
        if hits and not hx: viol.append((k,sorted(s for s,_ in hits),st["winner"]))
print(json.dumps({"files":len(files),"hands":hands,"decision_rows":rows,
 "action_rows_all_seats(tsumo|ron)":act_rows_any,
 "action_rows_in_student_scope":act_student,
 "paid_rows(recorded winner==seat)":paid_student,
 "blocked_rows(scope!=recorded winner)":act_student-paid_student,
 "hands_with_student_action_but_no_hand_indicator":len(viol),
 "violations_sample":viol[:8]}, ensure_ascii=False, indent=2))
