/** 题目详情与编辑页：查看 / 修改题干、选项、答案、解析、题型，并查看异常。 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Divider,
  IconButton,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import AddIcon from '@mui/icons-material/Add';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import { useNavigate, useParams } from 'react-router-dom';
import { api, toErrorMessage } from '../api/client';
import type { QType, QuestionDetailResult } from '../api/client';
import StatusChip from '../components/StatusChip';

const LETTERS = 'ABCDEFGH';
const QTYPE_OPTIONS: Array<{ value: QType; label: string }> = [
  { value: 'single', label: '单选题' },
  { value: 'multiple', label: '多选题' },
  { value: 'judge', label: '判断题' },
  { value: 'unknown', label: '未知题型' },
];

/** 题目详情页。 */
export default function QuestionDetailPage(): JSX.Element {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const questionId = Number(id);

  const [detail, setDetail] = useState<QuestionDetailResult | null>(null);
  const [stem, setStem] = useState('');
  const [answer, setAnswer] = useState('');
  const [analysis, setAnalysis] = useState('');
  const [qtype, setQtype] = useState<QType>('single');
  const [options, setOptions] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const applyDetail = useCallback((data: QuestionDetailResult): void => {
    setDetail(data);
    setStem(data.question.stem);
    setAnswer(data.question.answer);
    setAnalysis(data.question.analysis);
    setQtype(data.question.qtype);
    setOptions({ ...data.question.options });
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

  const letters = useMemo(() => {
    const keys = Object.keys(options).sort();
    const filled = new Set<string>(keys);
    // 保证至少展示 A-D 四个输入位
    LETTERS.split('')
      .slice(0, 4)
      .forEach((letter) => filled.add(letter));
    return Array.from(filled).sort();
  }, [options]);

  const nextLetter = useMemo(() => {
    const used = new Set(Object.keys(options));
    for (const letter of LETTERS) {
      if (!used.has(letter)) {
        return letter;
      }
    }
    return null;
  }, [options]);

  const save = async (): Promise<void> => {
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const payloadOptions: Record<string, string> = {};
      Object.entries(options).forEach(([letter, text]) => {
        if (text.trim()) {
          payloadOptions[letter] = text.trim();
        }
      });
      const data = await api.updateQuestion(questionId, {
        stem: stem.trim(),
        options: payloadOptions,
        answer: answer.trim(),
        analysis: analysis.trim(),
        qtype,
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

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
        <CircularProgress size={28} />
      </Box>
    );
  }

  if (!detail) {
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
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
        <IconButton size="small" onClick={() => navigate(-1)}>
          <ArrowBackIcon fontSize="small" />
        </IconButton>
        <Typography variant="h6" sx={{ fontWeight: 700 }}>
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
        <Stack spacing={2}>
          <TextField
            label="题干"
            multiline
            minRows={2}
            fullWidth
            value={stem}
            onChange={(event) => setStem(event.target.value)}
          />

          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              选项
            </Typography>
            <Stack spacing={1}>
              {letters.map((letter) => (
                <Stack key={letter} direction="row" spacing={1} alignItems="center">
                  <Typography variant="body2" sx={{ width: 20, color: 'text.secondary' }}>
                    {letter}
                  </Typography>
                  <TextField
                    size="small"
                    fullWidth
                    placeholder={`选项 ${letter} 内容`}
                    value={options[letter] ?? ''}
                    onChange={(event) =>
                      setOptions((previous) => ({ ...previous, [letter]: event.target.value }))
                    }
                  />
                  <IconButton
                    size="small"
                    onClick={() =>
                      setOptions((previous) => {
                        const next = { ...previous };
                        delete next[letter];
                        return next;
                      })
                    }
                    disabled={!options[letter]}
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
              disabled={!nextLetter}
              onClick={() => {
                if (nextLetter) {
                  setOptions((previous) => ({ ...previous, [nextLetter]: '' }));
                }
              }}
            >
              增加选项
            </Button>
          </Box>

          <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
            <TextField
              label="答案（多选写 ABD，判断写 √ / ×）"
              size="small"
              fullWidth
              value={answer}
              onChange={(event) => setAnswer(event.target.value)}
            />
            <TextField
              select
              size="small"
              label="题型"
              value={qtype}
              onChange={(event) => setQtype(event.target.value as QType)}
              sx={{ minWidth: 160 }}
            >
              {QTYPE_OPTIONS.map((option) => (
                <MenuItem key={option.value} value={option.value}>
                  {option.label}
                </MenuItem>
              ))}
            </TextField>
          </Stack>

          <TextField
            label="解析"
            multiline
            minRows={2}
            fullWidth
            value={analysis}
            onChange={(event) => setAnalysis(event.target.value)}
          />

          <Stack direction="row" spacing={1}>
            <Button variant="contained" onClick={() => void save()} disabled={saving}>
              保存并重算异常
            </Button>
            <Button variant="outlined" onClick={() => void load()} disabled={saving}>
              放弃修改
            </Button>
            <Box sx={{ flexGrow: 1 }} />
            <Button color="error" variant="text" onClick={() => void removeQuestion()} disabled={saving}>
              删除题目
            </Button>
          </Stack>
        </Stack>
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
