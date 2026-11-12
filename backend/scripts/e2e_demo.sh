#!/usr/bin/env bash
# 端到端演示：上传 → 诱导异常 → 异常校对修复 → 确认入库 → 搜索 → 导出
#
# 用法：cd backend && bash scripts/e2e_demo.sh
# 说明：
#   * 自行启动/关闭 uvicorn（默认端口 8125），使用临时数据目录，
#     不会污染仓库下的 data/（KEEP_DATA=1 可保留现场）；
#   * 每一步打印关键响应片段，最后汇总 PASS/FAIL，非 0 退出即视为演示失败。

set -u
set -o pipefail

cd "$(dirname "$0")/.." || exit 1

PORT="${PORT:-8125}"
BASE="http://127.0.0.1:${PORT}"
# 注意：本机 curl 是 Windows 原生版本，不认 MSYS 风格的 /c/... 绝对路径，
# 因此所有文件路径都使用「相对 backend/ 的路径」。
WORK=".e2e-run"
LOGFILE="${WORK}/uvicorn.log"
DATA_DIR="${WORK}/data"
DIRTY="${WORK}/in/dirty_basic.md"
BAD="${WORK}/in/no_heading.md"

PASS=0
FAIL=0

rm -rf "$WORK"
mkdir -p "$DATA_DIR" "${WORK}/in"

cleanup() {
  if [ -n "${UV_PID:-}" ] && kill -0 "$UV_PID" 2>/dev/null; then
    kill "$UV_PID" 2>/dev/null
    wait "$UV_PID" 2>/dev/null
  fi
  if [ "${KEEP_DATA:-0}" = "1" ]; then
    echo "（保留现场：${WORK}）"
  else
    rm -rf "$WORK"
  fi
}
trap cleanup EXIT

jget() {
  # jget '<python 表达式，d 为解析后的 JSON>' < file
  python -c "import json,sys;d=json.load(sys.stdin);print($1)"
}

check() {
  local name="$1" haystack="$2" needle="$3"
  if printf '%s' "$haystack" | grep -q -- "$needle"; then
    printf '    [PASS] %s\n' "$name"
    PASS=$((PASS + 1))
  else
    printf '    [FAIL] %s (期望包含: %s)\n' "$name" "$needle"
    printf '           实际: %s\n' "$haystack"
    FAIL=$((FAIL + 1))
  fi
}

# ---------------------------------------------------------------------------
# 造一份「带病」Markdown：缺答案 / 缺选项 / 选项不足 / 无题干
# ---------------------------------------------------------------------------
cat > "$DIRTY" <<'MD'
# 护理学基础（现场演示用脏数据）

## 1. [单选题]

脉搏短绌常见于哪种心律失常?

- **A.** 甲
- **B.** 乙
- **C.** 丙
- **D.** 丁

**答案**：（无答案）

## 2. [单选题]

青霉素过敏性休克首选药物是?

**答案**：A

## 3. [多选题]

口腔护理的目的包括?

- **A.** 保持口腔清洁
- **B.** 预防口腔感染

**答案**：AB

## 4. [单选题]

查

- **A.** 甲
- **B.** 乙
- **C.** 丙
- **D.** 丁

**答案**：A

## 5. [单选题]

大量不保留灌肠液面距肛门高度为?

- **A.** 20~30cm
- **B.** 40~60cm
- **C.** 70~80cm
- **D.** 90cm以上

**答案**：B

## 6. [判断题]

氧气筒内氧气可用尽后再更换。

**答案**：×
MD

cat > "$BAD" <<'MD'
这份稿件的题目标题（## ）在转换过程中全部丢失了。

无菌技术操作原则包括哪些？
铺备用床的目的是什么？
MD

echo "=================================================================="
echo " 私有护理题库 · 端到端演示（上传 → 异常 → 校对 → 入库 → 导出）"
echo "=================================================================="

# ---------------------------------------------------------------------------
echo
echo "[1/9] 启动后端（临时数据目录 ${DATA_DIR}）"
# ---------------------------------------------------------------------------
export TIKU_DATA_DIR="$DATA_DIR"
python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >"$LOGFILE" 2>&1 &
UV_PID=$!
for _ in $(seq 1 60); do
  if curl -sf "${BASE}/api/health" >/dev/null 2>&1; then break; fi
  sleep 0.5
done
if ! kill -0 "$UV_PID" 2>/dev/null; then
  echo "后端启动失败，日志："; cat "$LOGFILE"; exit 1
fi
HEALTH="$(curl -s "${BASE}/api/health")"
echo "  → ${HEALTH}"
check "健康检查 code=0" "$HEALTH" '"code":0'
check "FTS5 trigram 可用" "$HEALTH" '"fts_trigram":true'

# ---------------------------------------------------------------------------
echo
echo "[2/9] 上传 2 个文件（1 份脏 md + 1 份缺标题的坏 md）→ 解析并暂存"
# ---------------------------------------------------------------------------
UPLOAD="$(curl -s -X POST "${BASE}/api/imports" \
  -F "files[]=@${DIRTY}" \
  -F "files[]=@${BAD}")"
echo "  → $(printf '%s' "$UPLOAD" | head -c 460)"
check "上传成功（部分成功也返回 code=0）" "$UPLOAD" '"code":0'
IMPORT_ID="$(printf '%s' "$UPLOAD" | jget "d['data']['import_id']")"
ANOM_TOTAL="$(printf '%s' "$UPLOAD" | jget "sum(f['anomaly_count'] for f in d['data']['files'])")"
echo "  import_id=${IMPORT_ID}  解析出的异常总数=${ANOM_TOTAL}"
check "脏数据文件解析成功" "$UPLOAD" '"status":"parsed"'
check "已识别出异常（>=4 条）" "$(printf '%s' "$ANOM_TOTAL" | grep -E '^([4-9]|[1-9][0-9]+)$' || echo no)" '^[0-9]'
check "坏 md 被拒并给出定位到行的原因" "$UPLOAD" '未找到题目标题'

# ---------------------------------------------------------------------------
echo
echo "[3/9] 查看批次详情（入库前预览，status 应为 pending_confirm）"
# ---------------------------------------------------------------------------
DETAIL="$(curl -s "${BASE}/api/imports/${IMPORT_ID}")"
echo "  → $(printf '%s' "$DETAIL" | jget "d['data']['import']")"
echo "    文件明细：$(printf '%s' "$DETAIL" | jget "[(f['filename'], f['total_questions'], f['anomaly_count'], f['status'], f['error'][:18]) for f in d['data']['files']]")"
check "批次待确认" "$DETAIL" '"status":"pending_confirm"'
check "坏文件单独列为 failed 且带原因" "$DETAIL" '"status":"failed"'
check "坏文件保留 0 题记录便于溯源" "$DETAIL" '"total_questions":0'

# ---------------------------------------------------------------------------
echo
echo "[4/9] 打开异常清单（诱导出的 4 类异常）"
# ---------------------------------------------------------------------------
ANOM="$(curl -s --get "${BASE}/api/anomalies" --data-urlencode "handled=0" --data-urlencode "size=20")"
echo "  → summary=$(printf '%s' "$ANOM" | jget "d['data']['summary']")  total=$(printf '%s' "$ANOM" | jget "d['data']['total']")"
echo "    异常类型分布：$(printf '%s' "$ANOM" | jget "sorted({i['anomaly']['type'] for i in d['data']['items']})")"
check "缺答案 missing_answer" "$ANOM" 'missing_answer'
check "缺选项 missing_options" "$ANOM" 'missing_options'
check "选项不足 too_few_options" "$ANOM" 'too_few_options'
check "无题干 empty_stem" "$ANOM" 'empty_stem'

# 只挑「有题号」的那道缺答案题（无题号稿产生的是 parse_error 伪题）
MISSING_ANOM_ID="$(printf '%s' "$ANOM" | jget "[i['anomaly']['id'] for i in d['data']['items'] if i['anomaly']['type']=='missing_answer' and i['question']['question_no']=='1'][0]")"
MISSING_QID="$(printf '%s' "$ANOM" | jget "[i['question']['id'] for i in d['data']['items'] if i['anomaly']['type']=='missing_answer' and i['question']['question_no']=='1'][0]")"
echo "  待修复题目 question_id=${MISSING_QID}（异常 id=${MISSING_ANOM_ID}）"

# ---------------------------------------------------------------------------
echo
echo "[5/9] 校对修复：给「脉搏短绌」那道题补答案与解析（PUT → 异常全量重建）"
# ---------------------------------------------------------------------------
BEFORE="$(curl -s "${BASE}/api/questions/${MISSING_QID}")"
echo "  修复前 anomalies=$(printf '%s' "$BEFORE" | jget "[a['type'] for a in d['data']['anomalies']]")  answer=$(printf '%s' "$BEFORE" | jget "repr(d['data']['question']['answer'])")"
FIXED="$(curl -s -X PUT "${BASE}/api/questions/${MISSING_QID}" \
  -H 'Content-Type: application/json' \
  -d '{"stem":"脉搏短绌常见于哪种心律失常?","options":{"A":"房颤","B":"室颤","C":"房扑","D":"期前收缩"},"answer":"A","analysis":"脉搏短绌指同一单位时间内脉率少于心率，多见于心房颤动。","qtype":"single"}')"
echo "  → $(printf '%s' "$FIXED" | head -c 400)"
check "修复后 code=0" "$FIXED" '"code":0'
check "修复后题干保留" "$FIXED" '脉搏短绌'
check "修复后答案已写入" "$FIXED" '"answer":"A"'
check "修复后异常已清空" "$FIXED" '"anomalies":\[\]'

# ---------------------------------------------------------------------------
echo
echo "[6/9] 手动录题（POST /api/questions）并查看题库下拉（GET /api/libraries）"
# ---------------------------------------------------------------------------
CREATED="$(curl -s -X POST "${BASE}/api/questions" \
  -H 'Content-Type: application/json' \
  -d '{"stem":"患者男，65 岁，突发胸痛 2 小时，心电图提示 ST 段抬高。","options":{"A":"急性心肌梗死","B":"心绞痛","C":"肺栓塞","D":"主动脉夹层"},"answer":"A","analysis":"ST 段抬高提示透壁性心肌缺血。","qtype":"single","library_name":"手动录入"}')"
echo "  → $(printf '%s' "$CREATED" | head -c 460)"
check "手录成功 code=0" "$CREATED" '"code":0'
check "手录题直接 published" "$CREATED" '"status":"published"'
check "手录来源=题库名称" "$CREATED" '"name":"手动录入"'
NEW_QID="$(printf '%s' "$CREATED" | jget "d['data']['question']['id']")"
NEW_NO="$(printf '%s' "$CREATED" | jget "d['data']['question']['question_no']")"
echo "  新题 question_id=${NEW_QID}  题号=${NEW_NO}（留空自动取 1）"

LIBS="$(curl -s "${BASE}/api/libraries")"
echo "  → $(printf '%s' "$LIBS" | head -c 320)"
check "题库下拉含手录库" "$LIBS" '"kind":"manual"'
check "题库下拉含本次 md 导入来源" "$LIBS" '"kind":"md"'

# ---------------------------------------------------------------------------
echo
echo "[7/9] 确认入库（pending → published）"
# ---------------------------------------------------------------------------
COMMIT="$(curl -s -X POST "${BASE}/api/imports/${IMPORT_ID}/commit")"
echo "  → ${COMMIT}"
COMMITTED="$(printf '%s' "$COMMIT" | jget "d['data']['committed']")"
check "入库成功" "$COMMIT" '"code":0'
echo "  入库题数=${COMMITTED}"

# ---------------------------------------------------------------------------
echo
echo "[8/9] 搜索验证：2 字词（LIKE 降级）与 4 字词（FTS5 MATCH）+ 高亮"
# ---------------------------------------------------------------------------
S_SHORT="$(curl -s --get "${BASE}/api/search" --data-urlencode "q=房颤")"
echo "  q=房颤       → total=$(printf '%s' "$S_SHORT" | jget "d['data']['total']")  took_ms=$(printf '%s' "$S_SHORT" | jget "d['data']['took_ms']")"
echo "    高亮片段：$(printf '%s' "$S_SHORT" | jget "d['data']['items'][0]['highlight']['options']['A'] if d['data']['items'] else None")"
check "2 字词命中" "$S_SHORT" '"total":1'
check "命中词高亮为 <mark>" "$S_SHORT" '<mark>房颤</mark>'

S_LONG="$(curl -s --get "${BASE}/api/search" --data-urlencode "q=脉搏短绌 心律失常")"
echo "  q=脉搏短绌 心律失常 → total=$(printf '%s' "$S_LONG" | jget "d['data']['total']")  took_ms=$(printf '%s' "$S_LONG" | jget "d['data']['took_ms']")"
check "多关键词 AND 命中" "$S_LONG" '"total":1'
check "题干高亮" "$S_LONG" '<mark>脉搏短绌</mark>'

S_ANOM="$(curl -s --get "${BASE}/api/search" --data-urlencode "q=" --data-urlencode "only_anomaly=true")"
echo "  only_anomaly=true → total=$(printf '%s' "$S_ANOM" | jget "d['data']['total']")（修复过的题已不在其中）"
check "仅异常筛选可用" "$S_ANOM" '"total":'

# ---------------------------------------------------------------------------
echo
echo "[9/9] 导出 Markdown 与 JSON"
# ---------------------------------------------------------------------------
curl -s -D "${WORK}/md.head" -o "${WORK}/tiku.md" "${BASE}/api/export?format=md"
echo "  Content-Disposition: $(tr -d '\r' <"${WORK}/md.head" | grep -i content-disposition | head -1)"
echo "  导出大小：$(wc -c <"${WORK}/tiku.md") 字节"
echo "  ---- tiku.md 前 12 行 ----"
head -12 "${WORK}/tiku.md" | sed 's/^/  | /'
check "Markdown 标题正确" "$(head -1 "${WORK}/tiku.md")" '# 私有护理题库导出'
check "导出的题含答案" "$(cat "${WORK}/tiku.md")" '\*\*答案\*\*：A'
check "导出标注了异常标签" "$(cat "${WORK}/tiku.md")" '异常标记'

curl -s -o "${WORK}/tiku.json" "${BASE}/api/export?format=json"
echo "  JSON 导出：total=$(jget "d['total']" <"${WORK}/tiku.json") 首题题号=$(jget "d['items'][0]['question_no']" <"${WORK}/tiku.json")"
check "JSON 可解析且含 items" "$(head -c 60 "${WORK}/tiku.json")" '"total"'

STATS="$(curl -s "${BASE}/api/stats")"
echo
echo "统计面板 → $(printf '%s' "$STATS" | jget "d['data']")"
check "统计含已入库题数" "$STATS" '"total_published":'

echo
echo "=================================================================="
echo "端到端演示结果：PASS=${PASS}  FAIL=${FAIL}"
echo "=================================================================="
if [ "$FAIL" -gt 0 ]; then
  echo "---- uvicorn 日志尾部 ----"
  tail -40 "$LOGFILE"
  exit 1
fi
exit 0
