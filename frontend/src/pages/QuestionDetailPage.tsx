/** 题目详情与编辑页：查看 / 修改题干、选项、答案、解析、题型，并查看异常。 */
import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Divider,
  IconButton,
  Paper,
  Stack,
  Typography,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import { useNavigate, useParams } from 'react-router-dom';
import { api, toErrorMessage } from '../api/client';
import type { QuestionDetailResult } from '../api/client';
import QuestionForm from '../components/QuestionForm';
import type { QuestionFormValue } from '../components/QuestionForm';
import StatusChip from '../components/StatusChip';
import { filledOptions } from '../utils/questionForm';
import type { QuestionFormValidation } from '../utils/questionForm';

/** 详情 → 表单值的映射（题库名/题号在 edit 模式不参与提交）。 */
function toFormValue(detail: QuestionDetailResult): QuestionFormValue {
  return {
    stem: detail.question.stem,
    qtype: detail.question.qtype,
    options: { ...detail.question.options },
    answer: detail.question.answer,
    analysis: detail.question.analysis,
    libraryName: '',
    questionNo: detail.question.question_no ?? '',
  };
}

/**
 * 两个表单值是否等价（用于判断「有没有未保存的改动」）。
 *
 * 口径与提交 payload 对齐：文本字段按原样比较，选项统一走 ``filledOptions``
 * （trim + 丢弃空行 + 键大写排序），因此「加了一行空选项」不会被误判成脏数据。
 */
function isSameFormValue(left: QuestionFormValue, right: QuestionFormValue): boolean {
  if (left.qtype !== right.qtype) {
    return false;
  }
  if (left.stem !== right.stem || left.answer !== right.answer || left.analysis !== right.analysis) {
    return false;
  }
  const leftOptions = filledOptions(left.options);
  const rightOptions = filledOptions(right.options);
  const leftKeys = Object.keys(leftOptions);
  const rightKeys = Object.keys(rightOptions);
  if (leftKeys.length !== rightKeys.length) {
    return false;
  }
  for (const key of leftKeys) {
    if (leftOptions[key] !== rightOptions[key]) {
      return false;
    }
  }
  return true;
}

/**
 * 判断浏览器历史里是否存在可回退的上一页。
 *
 * react-router v6 会在 ``history.state`` 写入 ``idx``：直接通过 URL 打开页面时 ``idx === 0``，
 * 此时 ``navigate(-1)`` 会把用户弹出应用（或毫无反应），需要降级到首页。
 */
function canGoBack(): boolean {
  if (window.history.length <= 1) {
    return false;
  }
  const state: { idx?: number } | null = window.history.state as { idx?: number } | null;
  return (state?.idx ?? 0) > 0;
}

/** 题目详情页。 */
export default function QuestionDetailPage(): JSX.Element {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const questionId = Number(id);

  const [detail, setDetail] = useState<QuestionDetailResult | null>(null);
  const [value, setValue] = useState<QuestionFormValue | null>(null);
  const [valid, setValid] = useState(true);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const applyDetail = useCallback((data: QuestionDetailResult): void => {
    setDetail(data);
    setValue(toFormValue(data));
  }, []);

  const load = useCallback(async (): Promise<void> => {
    if (!Number.isFinite(questionId)) {
      setError('非法的题目 ID');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const data = await api.getQuestion(questionId);
      applyDetail(data);
    } catch (caught) {
      setError(toErrorMessage(caught));
    } finally {
      setLoading(false);
    }
  }, [questionId, applyDetail]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleValidity = useCallback((result: QuestionFormValidation): void => {
    setValid(result.valid);
  }, []);

  const save = async (): Promise<void> => {
    if (!value) {
      return;
    }
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const payloadOptions = filledOptions(value.options);
      const data = await api.updateQuestion(questionId, {
        stem: value.stem.trim(),
        options: payloadOptions,
        answer: value.answer.trim(),
        analysis: value.analysis.trim(),
        qtype: value.qtype,
      });
      applyDetail(data);
      setNotice('已保存，异常已按最新内容重算');
    } catch (caught) {
      setError(toErrorMessage(caught));
    } finally {
      setSaving(false);
    }
  };

  const removeQuestion = async (): Promise<void> => {
    setSaving(true);
    try {
      await api.deleteQuestion(questionId);
      navigate('/');
    } catch (caught) {
      setError(toErrorMessage(caught));
      setSaving(false);
    }
  };

  const markAnomaly = async (anomalyId: number, handled: boolean): Promise<void> => {
    try {
      await api.patchAnomaly(anomalyId, handled);
      await load();
    } catch (caught) {
      setError(toErrorMessage(caught));
    }
  };

  /**
   * 放弃修改：有未保存改动时先确认，然后返回上一页（无历史则回首页）。
   *
   * 与页面左上角返回箭头保持一致的「返回上一页」体验；只是多了一层防误触确认，
   * 避免用户辛苦改了一半的题目被一键丢掉。
   */
  const discardAndBack = useCallback((): void => {
    const dirty = detail !== null && value !== null && !isSameFormValue(value, toFormValue(detail));
    if (dirty && !window.confirm('放弃未保存的修改并返回？')) {
      return;
    }
    if (canGoBack()) {
      navigate(-1);
      return;
    }
    navigate('/');
  }, [detail, value, navigate]);

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
        <CircularProgress size={28} />
      </Box>
    );
  }

  if (!detail || !value) {
    return (
      <Box>
        <Alert severity="error">{error || '题目不存在'}</Alert>
        <Button sx={{ mt: 2 }} startIcon={<ArrowBackIcon />} onClick={() => navigate('/')}>
          返回搜索
        </Button>
      </Box>
    );
  }

  return (
    <Box>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }} flexWrap="wrap" useFlexGap>
        <IconButton size="small" onClick={() => navigate(-1)}>
          <ArrowBackIcon fontSize="small" />
        </IconButton>
        <Typography variant="h5" sx={{ fontWeight: 700 }}>
          题目 #{detail.question.id}
        </Typography>
        <StatusChip kind="qtype" value={detail.question.qtype} />
        <StatusChip kind="question_status" value={detail.question.status} />
        <Typography variant="caption" color="text.secondary">
          题号 {detail.question.question_no} · 来源 {detail.question.filename ?? '-'}
        </Typography>
      </Stack>

      {error ? (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      ) : null}
      {notice ? (
        <Alert severity="success" sx={{ mb: 2 }} onClose={() => setNotice('')}>
          {notice}
        </Alert>
      ) : null}

      <Paper variant="outlined" sx={{ p: 2.5, borderRadius: 2, mb: 2 }}>
        <QuestionForm
          mode="edit"
          value={value}
          onChange={setValue}
          onValidityChange={handleValidity}
          disabled={saving}
        >
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            <Button variant="contained" onClick={() => void save()} disabled={saving || !valid}>
              保存并重算异常
            </Button>
            <Button variant="outlined" onClick={discardAndBack} disabled={saving}>
              放弃修改
            </Button>
            <Box sx={{ flexGrow: 1 }} />
            <Button color="error" variant="text" onClick={() => void removeQuestion()} disabled={saving}>
              删除题目
            </Button>
          </Stack>
        </QuestionForm>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2, borderRadius: 2 }}>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          异常标记（{detail.anomalies.length}）
        </Typography>
        <Divider sx={{ mb: 1 }} />
        {detail.anomalies.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            该题没有异常标记 🎉
          </Typography>
        ) : (
          <Stack spacing={1}>
            {detail.anomalies.map((anomaly) => (
              <Stack key={anomaly.id} direction="row" spacing={1} alignItems="center">
                <StatusChip kind="anomaly" value={anomaly.type} muted={anomaly.handled === 1} />
                <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
                  {anomaly.detail}
                </Typography>
                <Button size="small" onClick={() => void markAnomaly(anomaly.id, anomaly.handled !== 1)}>
                  {anomaly.handled === 1 ? '未处理' : '标记已校对'}
                </Button>
              </Stack>
            ))}
          </Stack>
        )}
      </Paper>
    </Box>
  );
}
