/**
 * 题目表单的纯函数层（无 React 依赖，可被组件 / 测试 / 脚本直接复用）。
 *
 * 设计口径（与后端对齐的部分）：
 * - 答案在**存储层**始终是「大写、去重、A→H 升序的字母串」（选择题）或「√ / ×」（判断题），
 *   与后端 ``normalize_answer()`` / ``decide_qtype()`` 的输出同构，因此前端点选结果可以
 *   直接提交，不需要后端二次纠正。
 * - 选项字母固定 A–H（后端 ``OPTION_RE`` 同样只认 A-H）。
 *
 * **与后端有意保留的差异（勿“修正”）**：
 * 后端 ``POST /api/questions`` 只在题干为空（trim 后 0 字）时报错，1–2 字题干会**放行**并落
 * ``empty_stem`` 异常（导入历史数据里这种题大量存在，导入路径必须能过）。而录题/编辑是人工
 * 主动输入，前端用「题干至少 3 个字」**提前阻塞**，避免用户存进去再跑到异常页去改。两条路径
 * 面向的场景不同，故口径不同是刻意的；若将来要改，须同时改后端 7 条校验与
 * ``app/tests/test_api.py::test_create_question_*``。
 */
import type { QType } from '../api/client';

/** 选项字母表（与后端 A-H 一致）。 */
export const LETTERS = 'ABCDEFGH';

/** 判断题「正确」标记（全角根号，后端 ``JUDGE_TRUE``）。 */
export const JUDGE_TRUE = '√';

/** 判断题「错误」标记（全角乘号，后端 ``JUDGE_FALSE``）。 */
export const JUDGE_FALSE = '×';

/** 题库名长度上限（与后端 config.MAX_LIBRARY_NAME_LEN 一致）。 */
const MAX_LIBRARY_NAME = 120;

/** 题号长度上限（与后端 config.MAX_QUESTION_NO_LEN 一致）。 */
const MAX_QUESTION_NO = 32;

/** 题干最少字数（前端独有的提前拦截，见文件头说明）。 */
const MIN_STEM = 3;

/** 答案里允许出现的分隔符（会被忽略，不视为非法字符）。 */
const SEPARATOR_RE = /[\s,，、;；/|·.。]+/g;

/** 字母合法性判断（仅 A-H）。 */
const LETTER_RE = /^[A-H]$/;

/** 判断题「正确」的等价写法（含输入法常见的 正确 / 对 / 是 / T 等）。 */
const JUDGE_TRUE_WORDS = ['√', '对', '正确', '是', 'T', 'TRUE', 'V'];

/** 判断题「错误」的等价写法。 */
const JUDGE_FALSE_WORDS = ['×', '错', '错误', '否', 'F', 'FALSE', 'X'];

/**
 * 从答案串里取出合法字母。
 *
 * @param answer 答案串，可含分隔符（"b, a 、d"）。
 * @returns 大写、去重、A→H 升序的字母串（"ABD"）；无字母时返回空串。
 */
export function answerLetters(answer: string): string {
  const raw = (answer || '').toUpperCase().replace(SEPARATOR_RE, '');
  const seen: Record<string, true> = {};
  for (const char of raw) {
    if (LETTER_RE.test(char)) {
      seen[char] = true;
    }
  }
  return Array.from(LETTERS).filter((letter) => seen[letter] === true).join('');
}

/**
 * 答案里是否含有非法内容。
 *
 * 选择题：除 A–H 与分隔符之外的任何字符都算非法（例如「ABZ」「A√」）。
 * 判断题：只允许 √ / × 及其等价写法（正确/对/是/T 等），其余算非法。
 *
 * @param answer 答案串。
 * @param qtype 题型。
 * @returns true 表示存在非法字符。
 */
export function hasIllegalAnswerChars(answer: string, qtype: QType): boolean {
  const raw = (answer || '').trim();
  if (raw === '') {
    return false;
  }
  if (qtype === 'judge') {
    const upper = raw.toUpperCase();
    if (upper.length !== 1) {
      // 允许整词（正确 / 错误），只对单字符以外的写法做整词匹配
      return !(JUDGE_TRUE_WORDS.indexOf(upper) >= 0 || JUDGE_FALSE_WORDS.indexOf(upper) >= 0);
    }
    return !(JUDGE_TRUE_WORDS.indexOf(upper) >= 0 || JUDGE_FALSE_WORDS.indexOf(upper) >= 0);
  }
  const rest = raw.toUpperCase().replace(SEPARATOR_RE, '');
  for (const char of rest) {
    if (!LETTER_RE.test(char)) {
      return true;
    }
  }
  return false;
}

/**
 * 点击字母（单选 / 多选）。
 *
 * - single：已选中该字母 → 取消（空串）；否则 → 该字母。
 * - multiple：已含 → 移除（移除后为空则空串）；不含 → 并入并按 A→H 升序。
 * - judge：原样返回（判断题不响应字母点击）。
 * - unknown：原样返回（未知题型不提供点选）。
 *
 * @param answer 当前答案。
 * @param letter 被点击的字母（会被转成大写）。
 * @param qtype 题型。
 * @returns 新的答案串。
 */
export function toggleLetter(answer: string, letter: string, qtype: QType): string {
  if (qtype !== 'single' && qtype !== 'multiple') {
    return answer;
  }
  const target = (letter || '').toUpperCase();
  if (!LETTER_RE.test(target)) {
    return answer;
  }
  const current = answerLetters(answer);
  if (qtype === 'single') {
    return current === target ? '' : target;
  }
  const next = current.indexOf(target) >= 0
    ? current.replace(target, '')
    : answerLetters(current + target);
  return next;
}

/**
 * 判断题点选：同一个标记再点一次取消，√ 与 × 天然互斥。
 *
 * @param answer 当前答案。
 * @param mark 被点击的标记（√ / ×）。
 * @returns 新的答案串。
 */
export function setJudgeAnswer(answer: string, mark: string): string {
  const current = (answer || '').trim();
  const target = (mark || '').trim();
  if (target !== JUDGE_TRUE && target !== JUDGE_FALSE) {
    return answer;
  }
  return current === target ? '' : target;
}

/**
 * 手输答案归一化。
 *
 * 选择题：去空白与分隔符、转大写、去重、A→H 升序（"b, a 、d" → "ABD"）。
 * 判断题：只保留 √ / ×（"正确"→√、"错误"→×、"对"→√、"错"→×、"x"→×）；
 *   判断题答案通常由 :func:`setJudgeAnswer` 经点选产生，本分支用于粘贴/手输兜底，
 *   因此无法识别的内容一律归空，避免脏值进入答案列。
 *
 * @param raw 用户输入。
 * @param qtype 题型。
 * @returns 归一后的答案串。
 */
export function normalizeAnswerInput(raw: string, qtype: QType): string {
  const text = (raw || '').trim();
  if (qtype === 'judge') {
    if (text === '') {
      return '';
    }
    const upper = text.toUpperCase();
    if (JUDGE_TRUE_WORDS.indexOf(upper) >= 0 || text.indexOf(JUDGE_TRUE) >= 0) {
      return JUDGE_TRUE;
    }
    if (JUDGE_FALSE_WORDS.indexOf(upper) >= 0 || text.indexOf(JUDGE_FALSE) >= 0) {
      return JUDGE_FALSE;
    }
    return '';
  }
  return answerLetters(text);
}

/**
 * 取当前已填选项的「最大字母」。
 *
 * @param options 选项表。
 * @returns 最大字母（如 'C'）；无已填选项时返回空串。
 */
function maxFilledLetter(options: Record<string, string>): string {
  const letters = answerLetters(Object.keys(filledOptions(options)).join(''));
  return letters === '' ? '' : letters[letters.length - 1];
}

/**
 * 下一个可用字母（字母表里首个尚未出现在 options 的字母）。
 *
 * @param options 选项表。
 * @returns 可用字母；A–H 用尽返回 null。
 */
export function nextLetter(options: Record<string, string>): string | null {
  const filled = filledOptions(options);
  for (const letter of LETTERS) {
    if (!(letter in filled)) {
      return letter;
    }
  }
  return null;
}

/**
 * 只保留 trim 后非空的选项，键大写并按 A→H 排序（提交 payload 用）。
 *
 * 与后端 ``_filled_options()`` 同口径：空白选项不参与答案合法性判断，
 * 也不写入库（避免出现「D 是空串但答案含 D」这种脏数据）。
 *
 * @param options 选项表（键可能小写、值可能带空白）。
 * @returns 干净的选项表。
 */
export function filledOptions(options: Record<string, string>): Record<string, string> {
  const source = options || {};
  const result: Record<string, string> = {};
  for (const letter of LETTERS) {
    const value = source[letter] ?? source[letter.toLowerCase()] ?? '';
    const text = String(value).trim();
    if (text !== '') {
      result[letter] = text;
    }
  }
  return result;
}

/** 表单值（create 与 edit 共用的最小字段集）。 */
export interface QuestionFormValueLike {
  stem: string;
  qtype: QType;
  options: Record<string, string>;
  answer: string;
  analysis: string;
  libraryName: string;
  questionNo: string;
}

/** 校验结果：红字阻塞保存，黄字仅提示。 */
export interface QuestionFormValidation {
  valid: boolean;
  /** 阻塞保存的红字（顺序即展示顺序）。 */
  messages: string[];
  /** 不阻塞的黄字（如选项不足 4 项）。 */
  optionWarnings: string[];
}

/** 答案越界提示的主句（第 8/9 条共用）。 */
const ANSWER_LETTER_HINT = '答案只能是已填写选项的字母组合，判断题请用 √ / ×';

/**
 * 表单校验（create 与 edit 共用；只有 create 才校验题库名与题号长度）。
 *
 * 规则顺序即提示顺序：
 * 1. create：题库名为空 →「请选择或输入题库名称」
 * 2. create：题库名 > 120 字 →「题库名称最多 120 字」
 * 3. create：题号 > 32 字 →「题号最多 32 字」
 * 4. 题干 < 3 字 →「题干至少 3 个字」
 * 5. 选择题无已填选项 →「选择题至少需要 1 个选项」
 * 6. 答案为空 →「请填写答案或点击字母」
 * 7. 判断题答案不是 √ / × →「判断题答案只能是 √ 或 ×」
 * 8. 选择题答案含非法字符 → 越界提示主句
 * 9. 选择题答案含未填写选项的字母 → 主句 +「；」+「答案中的 X 不在已填写选项内」
 * 10. 黄字：选择题已填选项 1–3 个 →「选项不足 4 项，保存后会标记为异常」
 *
 * @param value 表单值。
 * @param mode 'create' 表示录题页，'edit' 表示详情页编辑。
 * @returns 校验结果。
 */
export function validateQuestionForm(
  value: QuestionFormValueLike,
  mode: 'create' | 'edit',
): QuestionFormValidation {
  const messages: string[] = [];
  const optionWarnings: string[] = [];

  const libraryName = (value.libraryName || '').trim();
  const questionNo = (value.questionNo || '').trim();
  const stem = (value.stem || '').trim();
  const answer = (value.answer || '').trim();
  const qtype = value.qtype;
  const isChoice = qtype === 'single' || qtype === 'multiple';
  const options = filledOptions(value.options);
  const optionCount = Object.keys(options).length;

  // 1) 题库名（仅录题页）
  if (mode === 'create' && libraryName === '') {
    messages.push('请选择或输入题库名称');
  }
  // 2) 题库名长度
  if (mode === 'create' && libraryName.length > MAX_LIBRARY_NAME) {
    messages.push(`题库名称最多 ${MAX_LIBRARY_NAME} 字`);
  }
  // 3) 题号长度
  if (mode === 'create' && questionNo.length > MAX_QUESTION_NO) {
    messages.push(`题号最多 ${MAX_QUESTION_NO} 字`);
  }
  // 4) 题干
  if (stem.length < MIN_STEM) {
    messages.push(`题干至少 ${MIN_STEM} 个字`);
  }
  // 5) 选项
  if (isChoice && optionCount === 0) {
    messages.push('选择题至少需要 1 个选项');
  }
  // 6) 答案非空
  if (answer === '') {
    messages.push('请填写答案或点击字母');
  } else if (qtype === 'judge') {
    // 7) 判断题答案只能是 √ / ×
    if (answer !== JUDGE_TRUE && answer !== JUDGE_FALSE) {
      messages.push('判断题答案只能是 √ 或 ×');
    }
  } else if (isChoice) {
    // 8) 非法字符
    if (hasIllegalAnswerChars(answer, qtype)) {
      messages.push(ANSWER_LETTER_HINT);
    } else {
      // 9) 越界字母
      const letters = answerLetters(answer);
      const missing: string[] = [];
      for (const letter of letters) {
        if (!(letter in options)) {
          missing.push(letter);
        }
      }
      if (missing.length > 0) {
        messages.push(`${ANSWER_LETTER_HINT}；答案中的 ${missing[0]} 不在已填写选项内`);
      }
    }
  }

  // 10) 黄字：选项不足 4 项
  if (qtype !== 'judge' && optionCount >= 1 && optionCount <= 3) {
    optionWarnings.push('选项不足 4 项，保存后会标记为异常');
  }

  return { valid: messages.length === 0, messages, optionWarnings };
}

export { maxFilledLetter };
