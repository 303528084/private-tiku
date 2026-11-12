/**
 * 手动录题页（``/questions/new``）。
 *
 * 为什么要有这一页：零散真题不该被迫先造一个 docx/JSON 文件。录入即入库（published），
 * 异常仍按导入同一套规则实时落 ``anomalies``，因此「选项不足 4 项」这类问题会出现在
 * 异常校对页，而不是被静默接受。
 */
import { useCallback, useEffect, useState } from 'react';
import { Alert, Box, Button, Paper, Stack, Typography } from '@mui/material';
import { useNavigate } from 'react-router-dom';
import { api, toErrorMessage } from '../api/client';
import type { LibraryItem } from '../api/client';
import { DEFAULT_LIBRARY_NAME, QTYPE_LABEL } from '../constants/import';
import QuestionForm, { emptyQuestionForm } from '../components/QuestionForm';
import type { QuestionFormValue } from '../components/QuestionForm';
import type { QuestionFormValidation } from '../utils/questionForm';
import { filledOptions } from '../utils/questionForm';

/** 手动录题页。 */
export default function QuestionCreatePage(): JSX.Element {
  const navigate = useNavigate();
  const [value, setValue] = useState<QuestionFormValue>(() => emptyQuestionForm());
  const [libraries, setLibraries] = useState<LibraryItem[]>([]);
  const [librariesLoading, setLibrariesLoading] = useState(true);
  const [valid, setValid] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const loadLibraries = useCallback(async (): Promise<void> => {
    setLibrariesLoading(true);
    try {
      const data = await api.listLibraries();
      setLibraries(data.items ?? []);
    } catch (caught) {
      // 下拉候选失败不阻塞录题：用户可以手输题库名
      setError(`题库列表加载失败：${toErrorMessage(caught)}`);
    } finally {
      setLibrariesLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadLibraries();
  }, [loadLibraries]);

  const handleValidity = useCallback((result: QuestionFormValidation): void => {
    setValid(result.valid);
  }, []);

  const submit = async (mode: 'view' | 'continue'): Promise<void> => {
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const payload = {
        stem: value.stem.trim(),
        options: filledOptions(value.options),
        answer: value.answer.trim(),
        analysis: value.analysis.trim(),
        qtype: value.qtype,
        library_name: value.libraryName.trim() || DEFAULT_LIBRARY_NAME,
        question_no: value.questionNo.trim() === '' ? null : value.questionNo.trim(),
      };
      const result = await api.createQuestion(payload);
      if (mode === 'view') {
        navigate(`/questions/${result.question.id}`);
        return;
      }
      // 连续录入：保留题库名与题型，清空题目内容
      setValue({
        stem: '',
        qtype: value.qtype,
        options: value.qtype === 'judge' ? {} : { A: '', B: '', C: '', D: '' },
        answer: '',
        analysis: '',
        libraryName: value.libraryName,
        questionNo: '',
      });
      setNotice(
        `已保存第 ${result.question.question_no} 题到「${result.library.name}」${
          result.library.created ? '（新建题库）' : ''
        }`,
      );
      void loadLibraries();
    } catch (caught) {
      setError(toErrorMessage(caught));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Box>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}>
        <Typography variant="h6" sx={{ fontWeight: 700 }}>
          手动录题
        </Typography>
        <Typography variant="caption" color="text.secondary">
          保存即入库（published），可连续录入；答案支持点选填入
        </Typography>
      </Stack>

      {error ? (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError('')}>
          {error}
        </Alert>
      ) : null}
      {notice ? (
        <Alert severity="success" sx={{ mb: 2 }} onClose={() => setNotice('')}>
          {notice}
        </Alert>
      ) : null}

      <Paper variant="outlined" sx={{ p: 2.5, borderRadius: 2 }}>
        <QuestionForm
          mode="create"
          value={value}
          onChange={setValue}
          libraries={libraries}
          librariesLoading={librariesLoading}
          onValidityChange={handleValidity}
          disabled={saving}
        >
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            <Button
              variant="contained"
              disabled={!valid || saving}
              onClick={() => void submit('view')}
            >
              保存
            </Button>
            <Button
              variant="contained"
              color="success"
              disabled={!valid || saving}
              onClick={() => void submit('continue')}
            >
              保存并继续录入
            </Button>
            <Button
              variant="outlined"
              disabled={saving}
              onClick={() => {
                setValue(emptyQuestionForm(value.libraryName || DEFAULT_LIBRARY_NAME));
                setNotice('');
                setError('');
              }}
            >
              清空
            </Button>
            <Box sx={{ flexGrow: 1 }} />
            <Typography variant="caption" color="text.secondary" sx={{ alignSelf: 'center' }}>
              当前题型：{QTYPE_LABEL[value.qtype] ?? value.qtype}
            </Typography>
          </Stack>
        </QuestionForm>
      </Paper>
    </Box>
  );
}
