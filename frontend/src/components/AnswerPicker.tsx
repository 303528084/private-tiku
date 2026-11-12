/**
 * 点选答案组件：用鼠标点字母（或判断题的 √ / ×）把答案填进去。
 *
 * 完全受控、无内部 state —— 答案的唯一真相在调用方的表单值里，
 * 因此「点选」与「手输答案框」天然双向同步（都由同一个 ``answer`` 驱动）。
 *
 * 交互口径见 docs/ARCHITECTURE.md §4.3：
 * - 单选：点字母即覆盖，再点同一个字母取消
 * - 多选：点字母并入并按 A→H 升序，再点已选字母移除
 * - 判断：只出 √ / × 两键，互斥，再点同一个取消
 * - 内容为空（或未定义）的选项按钮置灰，避免把不存在的选项填进答案
 */
import { Box, Chip, Stack, Typography } from '@mui/material';
import type { QType } from '../api/client';
import {
  JUDGE_FALSE,
  JUDGE_TRUE,
  LETTERS,
  answerLetters,
  filledOptions,
  maxFilledLetter,
  setJudgeAnswer,
  toggleLetter,
} from '../utils/questionForm';

/** 选择题至少展示到 D（四个选项是护理题的常态）。 */
const MIN_VISIBLE_LETTER = 'D';

export interface AnswerPickerProps {
  /** 题型；``unknown`` 时整块不渲染（历史脏数据没有可点选的语义）。 */
  qtype: QType;
  /** 受控答案：``'A'`` / ``'ABD'`` / ``'√'`` / ``'×'`` / ``''``。 */
  answer: string;
  /** 选项表，用于判定按钮是否置灰。 */
  options: Record<string, string>;
  /** 回传规范化后的答案（本组件不做任何二次清洗）。 */
  onChange: (next: string) => void;
  disabled?: boolean;
  /** 选择题最多展示到哪个字母；缺省 = max(已填最大字母, 'D')。 */
  maxLetter?: string;
}

/** 取字母在 A–H 中的序号，非法字母返回 -1。 */
function letterIndex(letter: string): number {
  return LETTERS.indexOf((letter || '').toUpperCase());
}

/** 计算需要渲染的字母列表（含置灰占位，便于「填了 D 才能点 D」的引导）。 */
function visibleLetters(options: Record<string, string>, maxLetter?: string): string[] {
  const filledTop = maxFilledLetter(options);
  let top = filledTop || 'A';
  if (maxLetter && letterIndex(maxLetter) > letterIndex(top)) {
    top = maxLetter.toUpperCase();
  }
  if (letterIndex(top) < letterIndex(MIN_VISIBLE_LETTER)) {
    top = MIN_VISIBLE_LETTER;
  }
  return LETTERS.slice(0, letterIndex(top) + 1).split('');
}

/** 点选答案组件。 */
export default function AnswerPicker({
  qtype,
  answer,
  options,
  onChange,
  disabled = false,
  maxLetter,
}: AnswerPickerProps): JSX.Element | null {
  if (qtype === 'unknown') {
    return null;
  }

  const caption = (
    <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 0.5 }}>
      点选答案：
    </Typography>
  );

  if (qtype === 'judge') {
    const marks: Array<{ mark: string; hint: string }> = [
      { mark: JUDGE_TRUE, hint: '正确' },
      { mark: JUDGE_FALSE, hint: '错误' },
    ];
    return (
      <Box>
        {caption}
        <Stack direction="row" spacing={1}>
          {marks.map(({ mark, hint }) => {
            const active = answer.trim() === mark;
            return (
              <Chip
                key={mark}
                label={mark}
                title={hint}
                aria-pressed={active}
                aria-label={`${hint}（${mark}）`}
                clickable={!disabled}
                disabled={disabled}
                color={active ? 'success' : 'default'}
                variant={active ? 'filled' : 'outlined'}
                onClick={() => onChange(setJudgeAnswer(answer, mark))}
              />
            );
          })}
        </Stack>
      </Box>
    );
  }

  const selected = answerLetters(answer);
  const filled = filledOptions(options);

  return (
    <Box>
      {caption}
      <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
        {visibleLetters(options, maxLetter).map((letter) => {
          const hasContent = letter in filled;
          const active = selected.indexOf(letter) >= 0;
          return (
            <Chip
              key={letter}
              label={letter}
              title={hasContent ? `选择 ${letter}` : `请先填写选项 ${letter} 的内容`}
              aria-pressed={active}
              clickable={!disabled && hasContent}
              disabled={disabled || !hasContent}
              color={active ? 'primary' : 'default'}
              variant={active ? 'filled' : 'outlined'}
              onClick={() => onChange(toggleLetter(answer, letter, qtype))}
            />
          );
        })}
      </Stack>
    </Box>
  );
}
