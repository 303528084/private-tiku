/**
 * 题目表单（受控组件）：手动录题页与题目详情页共用。
 *
 * 设计要点：
 * - **零内部状态**：所有字段都由 ``value`` 驱动、通过 ``onChange`` 回传，
 *   避免「页面 state 与表单 state 双份真相」导致的保存丢改动。
 * - 校验集中在 ``utils/questionForm.ts`` 的纯函数里，本组件只负责展示红线/黄线，
 *   并通过 ``onValidityChange`` 把 ``valid`` 告诉页面（用于禁用保存按钮）。
 * - 答案区同时提供「手输框」与「点选 Chip」：两者写同一个 ``answer`` 字段，
 *   因此天然双向同步（架构 §4.3）。
 */
import { useEffect, useMemo } from 'react';
import type { ReactNode } from 'react';
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import type { LibraryItem, QType } from '../api/client';
import { DEFAULT_LIBRARY_NAME, MAX_LIBRARY_NAME_LEN, MAX_QUESTION_NO_LEN, QTYPE_LABEL } from '../constants/import';
import {
  LETTERS,
  answerLetters,
  filledOptions,
  nextLetter,
  normalizeAnswerInput,
  validateQuestionForm,
} from '../utils/questionForm';
import type { QuestionFormValidation, QuestionFormValueLike } from '../utils/questionForm';
import AnswerPicker from './AnswerPicker';

/** 表单值（create 与 edit 共用的最小字段集）。 */
export type QuestionFormValue = QuestionFormValueLike;

/** 新建表单的初值工厂。 */
export function emptyQuestionForm(libraryName = DEFAULT_LIBRARY_NAME): QuestionFormValue {
  return {
    stem: '',
    qtype: 'single',
    options: { A: '', B: '', C: '', D: '' },
    answer: '',
    analysis: '',
    libraryName,
    questionNo: '',
  };
}

/** 新建模式下可选题型（手动录题不产生 unknown）。 */
const CREATE_QTYPES: QType[] = ['single', 'multiple', 'judge'];
/** 编辑模式下保留 unknown，兼容历史脏数据。 */
const EDIT_QTYPES: QType[] = ['single', 'multiple', 'judge', 'unknown'];

export interface QuestionFormProps {
  value: QuestionFormValue;
  onChange: (next: QuestionFormValue) => void;
  /** create 显示题库名/题号，edit 隐藏。 */
  mode: 'create' | 'edit';
  /** create 模式的题库名候选（可自由输入新建）。 */
  libraries?: LibraryItem[];
  librariesLoading?: boolean;
  qtypeOptions?: QType[];
  disabled?: boolean;
  /** 校验结果回调（页面据此禁用保存）。 */
  onValidityChange?: (result: QuestionFormValidation) => void;
  /** 动作区插槽（保存 / 删除 / 清空由页面注入）。 */
  children?: ReactNode;
}

/** 取字母序号，非法返回 -1。 */
function letterIndex(letter: string): number {
  return LETTERS.indexOf((letter || '').toUpperCase());
}

/**
 * 计算选项区要渲染的字母行。
 *
 * 始终展示到「已填最大字母」与 D 的较大者；「增加选项」按钮会把新字母以空串写入
 * ``options``，因此额外空行只由用户显式添加产生（不在渲染时凭空多出一行）。
 * 删除选项时**不重排字母**（删 C 后 D 仍是 D），与详情页历史行为一致。
 */
function visibleOptionLetters(options: Record<string, string>): string[] {
  const keys = Object.keys(options).filter((key) => key.length === 1);
  let top = 'D';
  keys.forEach((key) => {
    if (letterIndex(key) > letterIndex(top)) {
      top = key.toUpperCase();
    }
  });
  const filled = filledOptions(options);
  const filledKeys = Object.keys(filled);
  const filledTop = filledKeys.length > 0 ? filledKeys[filledKeys.length - 1] : '';
  if (letterIndex(filledTop) > letterIndex(top)) {
    top = filledTop;
  }
  return LETTERS.slice(0, letterIndex(top) + 1).split('');
}

/** 题目表单。 */
export default function QuestionForm({
  value,
  onChange,
  mode,
  libraries = [],
  librariesLoading = false,
  qtypeOptions,
  disabled = false,
  onValidityChange,
  children,
}: QuestionFormProps): JSX.Element {
  const validation = useMemo(() => validateQuestionForm(value, mode), [value, mode]);
  const optionLetters = useMemo(() => visibleOptionLetters(value.options), [value.options]);
  const resolvedQtypes = qtypeOptions ?? (mode === 'create' ? CREATE_QTYPES : EDIT_QTYPES);

  useEffect(() => {
    onValidityChange?.(validation);
  }, [validation, onValidityChange]);

  const patch = (partial: Partial<QuestionFormValue>): void => {
    onChange({ ...value, ...partial });
  };

  const changeQtype = (next: QType): void => {
    if (next === value.qtype) {
      return;
    }
    if (next === 'judge') {
      // 判断题没有选项与字母答案，切换时一并清空，避免脏值随保存入库
      patch({ qtype: next, options: {}, answer: '' });
      return;
    }
    const letters = Object.keys(filledOptions(value.options));
    const restored: Record<string, string> = letters.length > 0
      ? { ...filledOptions(value.options) }
      : { A: '', B: '', C: '', D: '' };
    patch({
      qtype: next,
      options: restored,
      answer: value.qtype === 'judge' ? '' : answerLetters(value.answer),
    });
  };

  const setOption = (letter: string, text: string): void => {
    patch({ options: { ...value.options, [letter]: text } });
  };

  const removeOption = (letter: string): void => {
    const nextOptions: Record<string, string> = { ...value.options };
    delete nextOptions[letter];
    // 答案里被删掉的字母同步移除（否则保存后必报「答案不在已填写选项内」）
    const nextAnswer = answerLetters(value.answer).replace(letter, '');
    patch({ options: nextOptions, answer: nextAnswer });
  };

  const addOption = (): void => {
    const letter = nextLetter(value.options);
    if (letter) {
      patch({ options: { ...value.options, [letter]: '' } });
    }
  };

  const libraryNames = useMemo(
    () => Array.from(new Set(libraries.map((item) => item.name).filter(Boolean))),
    [libraries],
  );

  return (
    <Stack spacing={2}>
      {mode === 'create' ? (
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
          <Autocomplete
            freeSolo
            options={libraryNames}
            loading={librariesLoading}
            value={value.libraryName}
            inputValue={value.libraryName}
            onInputChange={(_, next) => patch({ libraryName: next })}
            onChange={(_, next) => patch({ libraryName: typeof next === 'string' ? next : '' })}
            sx={{ flex: 1 }}
            renderInput={(params) => (
              <TextField
                {...params}
                label="题库名称"
                required
                size="small"
                disabled={disabled}
                helperText={`默认「${DEFAULT_LIBRARY_NAME}」，可下拉选已有题库或直接输入新建（最多 ${MAX_LIBRARY_NAME_LEN} 字）`}
              />
            )}
          />
          <TextField
            label="题号（留空自动编号）"
            size="small"
            value={value.questionNo}
            disabled={disabled}
            onChange={(event) => patch({ questionNo: event.target.value })}
            inputProps={{ maxLength: MAX_QUESTION_NO_LEN }}
            sx={{ width: { xs: '100%', md: 220 } }}
          />
        </Stack>
      ) : null}

      <TextField
        select
        size="small"
        label="题型"
        value={value.qtype}
        disabled={disabled}
        onChange={(event) => changeQtype(event.target.value as QType)}
        sx={{ width: { xs: '100%', md: 200 } }}
      >
        {resolvedQtypes.map((item) => (
          <MenuItem key={item} value={item}>
            {QTYPE_LABEL[item] ?? item}
          </MenuItem>
        ))}
      </TextField>

      <TextField
        label="题干"
        required
        multiline
        minRows={2}
        fullWidth
        value={value.stem}
        disabled={disabled}
        onChange={(event) => patch({ stem: event.target.value })}
      />

      {value.qtype === 'judge' ? (
        <Alert severity="info" variant="outlined">
          判断题不需要选项，直接在下方点选 √ / × 即可。
        </Alert>
      ) : (
        <Box>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            选项
          </Typography>
          <Stack spacing={1}>
            {optionLetters.map((letter) => (
              <Stack key={letter} direction="row" spacing={1} alignItems="center">
                <Typography variant="body2" sx={{ width: 20, color: 'text.secondary' }}>
                  {letter}
                </Typography>
                <TextField
                  size="small"
                  fullWidth
                  placeholder={`选项 ${letter} 内容`}
                  value={value.options[letter] ?? ''}
                  disabled={disabled}
                  onChange={(event) => setOption(letter, event.target.value)}
                />
                <IconButton
                  size="small"
                  disabled={disabled || !String(value.options[letter] ?? '').trim()}
                  onClick={() => removeOption(letter)}
                  title={`删除选项 ${letter}`}
                >
                  <DeleteOutlineIcon fontSize="small" />
                </IconButton>
              </Stack>
            ))}
          </Stack>
          <Button
            size="small"
            startIcon={<AddIcon />}
            sx={{ mt: 1 }}
            disabled={disabled || nextLetter(value.options) === null}
            onClick={addOption}
          >
            增加选项
          </Button>
        </Box>
      )}

      <Box>
        <TextField
          label="答案（可手输，也可在下方点选）"
          size="small"
          fullWidth
          value={value.answer}
          disabled={disabled}
          onChange={(event) => patch({ answer: normalizeAnswerInput(event.target.value, value.qtype) })}
          helperText={
            value.qtype === 'judge'
              ? '判断题答案：√ 或 ×'
              : '选择题答案：字母组合（多选如 ABD，点选会自动按 A→H 排序）'
          }
        />
        <Box sx={{ mt: 1 }}>
          <AnswerPicker
            qtype={value.qtype}
            answer={value.answer}
            options={value.options}
            disabled={disabled}
            onChange={(next) => patch({ answer: next })}
          />
        </Box>
      </Box>

      <TextField
        label="解析"
        multiline
        minRows={2}
        fullWidth
        value={value.analysis}
        disabled={disabled}
        onChange={(event) => patch({ analysis: event.target.value })}
      />

      {validation.messages.length > 0 ? (
        <Box>
          {validation.messages.map((message) => (
            <Typography key={message} variant="caption" color="error.main" display="block">
              · {message}
            </Typography>
          ))}
        </Box>
      ) : null}
      {validation.optionWarnings.length > 0 ? (
        <Box>
          {validation.optionWarnings.map((message) => (
            <Typography key={message} variant="caption" color="warning.main" display="block">
              · {message}
            </Typography>
          ))}
        </Box>
      ) : null}

      {children}
    </Stack>
  );
}
