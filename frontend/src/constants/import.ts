/**
 * 导入格式契约（v1.3 起仅支持 JSON / Markdown）。
 *
 * 本文件的字面量是**前后端唯一契约**：后端 backend/app/config.py 与
 * backend/app/tests/test_text_parity.py 会读取本文件做断言，因此
 * 修改 ACCEPT_EXT / DEFAULT_LIBRARY_NAME / unsupportedExtMessage 的文案时
 * 必须同步修改后端常量，并跑 test_text_parity.py。
 */

/** 允许上传的扩展名（小写带点）—— 必须与后端 config.ALLOWED_EXT 完全一致。 */
export const ACCEPT_EXT = ['.json', '.md', '.markdown'];

/** 手录时的缺省题库名 —— 必须与后端 config.DEFAULT_LIBRARY_NAME 一致。 */
export const DEFAULT_LIBRARY_NAME = '手动录入';

/** 题库名 / 题号长度上限（与后端 config 同一口径，仅用于前端预校验提示）。 */
export const MAX_LIBRARY_NAME_LEN = 120;
export const MAX_QUESTION_NO_LEN = 32;

/** 题型展示名（含 unknown，编辑页仍需可见）。 */
export const QTYPE_LABEL: Record<string, string> = {
  single: '单选题',
  multiple: '多选题',
  judge: '判断题',
  unknown: '未知题型',
};

/** 模板文件名（下载时使用）。 */
export const MD_TEMPLATE_FILENAME = 'tiku-template.md';

/**
 * 旧格式拒绝文案。
 *
 * 模板必须与后端 config.UNSUPPORTED_EXT_TEMPLATE 逐字一致（含标点与空格），
 * 否则前端预拦截与后端兜底会出现两种说法。
 *
 * @param ext 带点的后缀，如 ".docx"；也可传文件名，函数会自动取后缀。
 * @returns 可直接展示给用户的提示文案。
 */
export function unsupportedExtMessage(ext: string): string {
  return `不支持的文件类型「${ext}」：v1.3 起仅支持 .json / .md 导入。已有的 docx/txt/pdf 题库不受影响，可直接搜索与导出。`;
}

/**
 * 取文件名后缀（小写、含点）。
 *
 * @param filename 文件名，如 "题库-2024.MD"。
 * @returns 后缀，如 ".md"；无后缀时返回空串。
 */
export function fileExt(filename: string): string {
  const dot = filename.lastIndexOf('.');
  return dot < 0 ? '' : filename.slice(dot).toLowerCase();
}

/**
 * 判断文件是否属于本期支持的导入格式。
 *
 * @param filename 文件名。
 * @returns true 表示可以上传。
 */
export function isSupportedFile(filename: string): boolean {
  return ACCEPT_EXT.indexOf(fileExt(filename)) >= 0;
}

/** 组件 accept 属性值（"..."），供 input / dropzone 直接使用。 */
export const ACCEPT_ATTR = ACCEPT_EXT.join(',');

/** 人类可读的格式说明，用于标题与提示（"JSON / MD"）。 */
export const ACCEPT_LABEL = 'JSON / MD';

/**
 * 最小可用 Markdown 题库模板：与后端导出格式同构，可直接上传。
 *
 * 结构约定：
 * - 每题以 "## 题号. [题型]" 开头，题型取 单选题 / 多选题 / 判断题；
 * - 题干紧随其后（空行分隔）；
 * - 选项每行一个，写作 "- **A.** 选项内容"；
 * - "**答案**：A"；多选题写连写字母如 "**答案**：AC"；判断题写 √ 或 ×；
 * - "**解析**：..."（可省略）。
 */
export const MD_TEMPLATE = `# 我的题库

## 1. [单选题]

正常成人安静状态下的心率范围是（  ）

- **A.** 40～60 次/分
- **B.** 60～100 次/分
- **C.** 100～120 次/分
- **D.** 120～140 次/分

**答案**：B

**解析**：成人安静状态下正常心率为 60～100 次/分。

---

## 2. [多选题]

下列属于医院内感染的是（  ）

- **A.** 入院时已存在的感染
- **B.** 住院期间获得并发生的感染
- **C.** 住院期间获得、出院后发生的感染
- **D.** 医务人员在工作中获得的感染

**答案**：BCD

**解析**：医院内感染指住院期间获得并发生的感染，含出院后发病与职业暴露。

---

## 3. [判断题]

测脉搏时若患者刚活动过，应休息 20～30 分钟后再测量。（  ）

**答案**：√

**解析**：活动、情绪激动均会使脉率增快，需休息后测量。

---
`;

/**
 * 触发浏览器下载 Markdown 模板（tiku-template.md）。
 *
 * 纯前端实现：Blob + 临时 a 标签，不依赖后端接口，也不依赖 SSR 环境。
 * 通过 URL.revokeObjectURL 释放内存，避免长时间占用。
 */
export function downloadMarkdownTemplate(): void {
  const blob = new Blob([MD_TEMPLATE], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = MD_TEMPLATE_FILENAME;
  anchor.style.display = 'none';
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}
