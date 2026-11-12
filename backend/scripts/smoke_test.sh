#!/usr/bin/env bash
# 冒烟测试：真实闭环（上传 → 确认入库 → 搜索高亮 → 异常校对 → 编辑重算 → 导出 → 静态托管）
#
# 用法：cd backend && bash scripts/smoke_test.sh
# 说明：脚本会自行启动/关闭 uvicorn（端口默认 8123），并使用临时数据目录，
#       不会污染仓库下的 data/（可用 KEEP_DATA=1 保留以便排查）。
set -u
set -o pipefail

cd "$(dirname "$0")/.." || exit 1

PORT="${PORT:-8123}"
BASE="http://127.0.0.1:${PORT}"
WORK="$(pwd)/.smoke-run"
LOGFILE="${WORK}/uvicorn.log"
PASS=0
FAIL=0

rm -rf "$WORK"
mkdir -p "$WORK"

cleanup() {
  if [ -n "${UV_PID:-}" ] && kill -0 "$UV_PID" 2>/dev/null; then
    kill "$UV_PID" 2>/dev/null
    wait "$UV_PID" 2>/dev/null
  fi
  if [ "${KEEP_DATA:-0}" = "1" ]; then
    echo "（保留临时数据：${WORK}）"
  else
    rm -rf "$WORK"
  fi
}
trap cleanup EXIT

check() {
  local name="$1" haystack="$2" needle="$3"
  if printf '%s' "$haystack" | grep -q -- "$needle"; then
    printf '  [PASS] %s\n' "$name"
    PASS=$((PASS + 1))
  else
    printf '  [FAIL] %s (期望包含: %s)\n' "$name" "$needle"
    printf '         实际: %s\n' "$haystack"
    FAIL=$((FAIL + 1))
  fi
}

json_field() {
  # json_field '<python 表达式，d 为解析后的 JSON>' < file
  python -c "import json,sys;d=json.load(sys.stdin);print($1)"
}

echo "== 0. 启动后端（临时数据目录：${WORK}/data）=="
export TIKU_DATA_DIR="${WORK}/data"
python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >"$LOGFILE" 2>&1 &
UV_PID=$!
for _ in $(seq 1 60); do
  if curl -sf "${BASE}/api/health" >/dev/null 2>&1; then break; fi
  sleep 0.5
done
if ! kill -0 "$UV_PID" 2>/dev/null; then
  echo "后端启动失败，日志："; cat "$LOGFILE"; exit 1
fi
echo "  PID=$UV_PID  日志=$LOGFILE"

echo
echo "== 1. GET /api/health =="
HEALTH="$(curl -s "${BASE}/api/health")"
echo "  $HEALTH"
check "health code=0" "$HEALTH" '"code":0'
check "health status=ok" "$HEALTH" '"status":"ok"'
check "fts trigram 可用" "$HEALTH" '"fts_trigram":true'

echo
echo "== 2. POST /api/imports（3 个 md 文件：基础 + 变体 + 缺标题的坏文件）=="
UPLOAD="$(curl -s -X POST "${BASE}/api/imports" \
  -F "files[]=@app/tests/fixtures/sample_basic.md" \
  -F "files[]=@app/tests/fixtures/sample_variants.md" \
  -F "files[]=@app/tests/fixtures/sample_messy.md")"
echo "  ${UPLOAD:0:520}"
check "上传返回 code=0（部分成功）" "$UPLOAD" '"code":0'
check "2 个文件解析成功" "$UPLOAD" '"status":"parsed"'
check "1 个坏文件被拒并给出原因" "$UPLOAD" '"status":"failed"'
FILE_COUNT="$(printf '%s' "$UPLOAD" | json_field "len(d['data']['files'])")"
TOTAL_Q="$(printf '%s' "$UPLOAD" | json_field "sum(f['total_questions'] for f in d['data']['files'])")"
IMPORT_ID="$(printf '%s' "$UPLOAD" | json_field "d['data']['import_id']")"
echo "  import_id=$IMPORT_ID  文件数=$FILE_COUNT  题目总数=$TOTAL_Q"
if [ "$FILE_COUNT" -eq 3 ] && [ "$TOTAL_Q" -ge 16 ]; then
  echo "  [PASS] 文件数=3 且题目总数 >= 16"; PASS=$((PASS + 1))
else
  echo "  [FAIL] 文件数=$FILE_COUNT 题目总数=$TOTAL_Q"; FAIL=$((FAIL + 1))
fi

echo
echo "== 3. GET /api/imports/{id}（导入详情）=="
DETAIL="$(curl -s "${BASE}/api/imports/${IMPORT_ID}")"
check "详情含 status=pending_confirm" "$DETAIL" '"status":"pending_confirm"'
check "详情含 3 个文件明细" "$DETAIL" '"file_id":3'
check "详情含异常计数" "$DETAIL" '"anomaly_count":2'

echo
echo "== 4. POST /api/imports/{id}/commit（确认入库）=="
COMMIT="$(curl -s -X POST "${BASE}/api/imports/${IMPORT_ID}/commit")"
echo "  $COMMIT"
check "commit 返回提交数" "$COMMIT" '"committed":'
COMMITTED="$(printf '%s' "$COMMIT" | json_field "d['data']['committed']")"
if [ "$COMMITTED" -lt 16 ]; then
  echo "  [FAIL] 入库题数过少：$COMMITTED"; FAIL=$((FAIL + 1))
else
  echo "  [PASS] 入库题数 = $COMMITTED"; PASS=$((PASS + 1))
fi

echo
echo "== 5. GET /api/search?q=房颤（高亮 + took_ms）=="
SEARCH="$(curl -s --get "${BASE}/api/search" --data-urlencode "q=房颤")"
echo "  ${SEARCH:0:420}"
check "搜索 code=0" "$SEARCH" '"code":0'
check "命中 >=1 条" "$SEARCH" '"total":'
check "返回 took_ms" "$SEARCH" '"took_ms":'
check "返回 <mark> 高亮" "$SEARCH" '<mark>房颤</mark>'
SEARCH_TOTAL="$(printf '%s' "$SEARCH" | json_field "d['data']['total']")"
if [ "$SEARCH_TOTAL" -ge 1 ]; then
  echo "  [PASS] 命中 $SEARCH_TOTAL 条"; PASS=$((PASS + 1))
else
  echo "  [FAIL] 未命中任何题目"; FAIL=$((FAIL + 1))
fi
QUESTION_ID="$(printf '%s' "$SEARCH" | json_field "d['data']['items'][0]['id']")"
echo "  question_id=$QUESTION_ID"

echo
echo "== 5b. GET /api/search?q=正常成人腋温（>=3 字 → FTS5 MATCH 路径）=="
SEARCH_FTS="$(curl -s --get "${BASE}/api/search" --data-urlencode "q=正常成人腋温")"
check "FTS 多字词命中" "$SEARCH_FTS" '"total":1'
check "FTS 耗时字段正常" "$SEARCH_FTS" '"took_ms":'

echo
echo "== 6. GET /api/search?q=房颤&type=multiple（筛选无结果校验）=="
SEARCH_MULTI="$(curl -s --get "${BASE}/api/search" --data-urlencode "q=房颤" --data-urlencode "type=multiple")"
check "多选筛选 total=0" "$SEARCH_MULTI" '"total":0'

echo
echo "== 7. GET /api/anomalies?handled=0（异常清单）=="
ANOM="$(curl -s --get "${BASE}/api/anomalies" --data-urlencode "handled=0" --data-urlencode "size=5")"
check "异常 code=0" "$ANOM" '"code":0'
check "含缺答案异常" "$ANOM" 'missing_answer'
check "含选项不足异常" "$ANOM" 'too_few_options'
ANOM_TOTAL="$(printf '%s' "$ANOM" | json_field "d['data']['total']")"
ANOM_ID="$(printf '%s' "$ANOM" | json_field "[i['anomaly']['id'] for i in d['data']['items'] if i['anomaly']['type']=='missing_answer'][0]")"
ANOM_QID="$(printf '%s' "$ANOM" | json_field "[i['question']['id'] for i in d['data']['items'] if i['anomaly']['type']=='missing_answer'][0]")"
echo "  未处理异常总数=$ANOM_TOTAL  missing_answer anomaly_id=$ANOM_ID question_id=$ANOM_QID"

echo
echo "== 8. PATCH /api/anomalies/{id}（标记已处理）=="
PATCH="$(curl -s -X PATCH "${BASE}/api/anomalies/${ANOM_ID}" \
  -H 'Content-Type: application/json' -d '{"handled":true}')"
echo "  $PATCH"
check "标记后 handled=1" "$PATCH" '"handled":1'

echo
echo "== 9. PUT /api/questions/{id}（补答案并重算异常）=="
PUT="$(curl -s -X PUT "${BASE}/api/questions/${ANOM_QID}" \
  -H 'Content-Type: application/json' \
  -d '{"stem":"静脉输液时发生空气栓塞应立即采取的体位是？","options":{"A":"左侧卧位并头低足高","B":"右侧卧位","C":"半坐卧位","D":"平卧位"},"answer":"A","analysis":"左侧卧位头低足高可使气体浮于右心室尖部。","qtype":"single"}')"
echo "  ${PUT:0:420}"
check "编辑后 code=0" "$PUT" '"code":0'
check "编辑后答案已更新" "$PUT" '"answer":"A"'
check "编辑后该题异常已清空" "$PUT" '"anomalies":\[\]'

echo
echo "== 10. GET /api/export?format=md（文件流）=="
EXPORT_HEAD="$(curl -s -D - -o "${WORK}/export.md" "${BASE}/api/export?format=md" | tr -d '\r')"
check "Content-Disposition 存在" "$EXPORT_HEAD" 'content-disposition: attachment'
check "content-type markdown" "$EXPORT_HEAD" 'text/markdown'
check "导出正文含题目" "$(head -c 400 "${WORK}/export.md")" '# 私有护理题库导出'
echo "  导出文件大小：$(wc -c <"${WORK}/export.md") 字节"

echo
echo "== 11. GET /api/export?format=json =="
curl -s -o "${WORK}/export.json" "${BASE}/api/export?format=json"
check "JSON 导出含 items" "$(head -c 200 "${WORK}/export.json")" '"items"'

echo
echo "== 12. GET /api/stats =="
STATS="$(curl -s "${BASE}/api/stats")"
echo "  $STATS"
check "stats 含 total_published" "$STATS" '"total_published":'

echo
echo "== 13. GET /（前端产物由 StaticFiles 托管）=="
ROOT_CODE="$(curl -s -o "${WORK}/index.html" -w '%{http_code}' "${BASE}/")"
echo "  HTTP $ROOT_CODE"
check "首页返回 200" "$ROOT_CODE" '200'
check "首页为 SPA HTML" "$(cat "${WORK}/index.html")" '<div id="root">'

echo
echo "== 14. 404 与错误码规范 =="
NOT_FOUND="$(curl -s "${BASE}/api/questions/999999")"
echo "  $NOT_FOUND"
check "不存在的题目返回 40401" "$NOT_FOUND" '"code":40401'

echo
echo "==================================================="
echo "冒烟测试结果：PASS=$PASS  FAIL=$FAIL"
echo "==================================================="
if [ "$FAIL" -gt 0 ]; then
  echo "---- uvicorn 日志尾部 ----"
  tail -40 "$LOGFILE"
  exit 1
fi
exit 0
