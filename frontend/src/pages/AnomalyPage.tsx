/** 异常校对页：按类型/处理状态筛选，逐条标记已处理。 */
import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  FormControl,
  InputLabel,
  MenuItem,
  Pagination,
  Paper,
  Select,
  Stack,
  Typography,
} from '@mui/material';
import type { SelectChangeEvent } from '@mui/material';
import { api, toErrorMessage } from '../api/client';
import type { AnomalyListResult, AnomalyType } from '../api/client';
import QuestionCard from '../components/QuestionCard';
import StatusChip from '../components/StatusChip';

const PAGE_SIZE = 10;

const TYPE_OPTIONS: Array<{ value: AnomalyType | ''; label: string }> = [
  { value: '', label: '全部类型' },
  { value: 'missing_answer', label: '缺答案' },
  { value: 'missing_options', label: '缺选项' },
  { value: 'too_few_options', label: '选项不足' },
  { value: 'empty_stem', label: '无题干' },
  { value: 'duplicate_number', label: '题号重复' },
  { value: 'parse_error', label: '解析异常' },
];

/** 异常校对页。 */
export default function AnomalyPage(): JSX.Element {
  const [type, setType] = useState<AnomalyType | ''>('');
  const [handled, setHandled] = useState<string>('0');
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<AnomalyListResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(
    async (nextPage: number): Promise<void> => {
      setLoading(true);
      setError('');
      try {
        const data = await api.listAnomalies({
          type,
          handled: handled === '' ? null : (Number(handled) as 0 | 1),
          page: nextPage,
          size: PAGE_SIZE,
        });
        setResult(data);
        setPage(nextPage);
      } catch (caught) {
        setError(toErrorMessage(caught));
      } finally {
        setLoading(false);
      }
    },
    [type, handled],
  );

  useEffect(() => {
    void load(1);
  }, [load]);

  const markHandled = async (anomalyId: number, next: boolean): Promise<void> => {
    setBusyId(anomalyId);
    try {
      await api.patchAnomaly(anomalyId, next);
      await load(page);
    } catch (caught) {
      setError(toErrorMessage(caught));
    } finally {
      setBusyId(null);
    }
  };

  const totalPages = result ? Math.max(1, Math.ceil(result.total / PAGE_SIZE)) : 1;

  return (
    <Box>
      <Typography variant="h5" sx={{ fontWeight: 700, mb: 0.5 }}>
        异常校对
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        解析异常清单：可筛选类型、逐题查看并标记已处理；编辑题目后异常会自动重算。
      </Typography>

      <Paper variant="outlined" sx={{ p: 2, mb: 2, borderRadius: 2 }}>
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} alignItems={{ md: 'center' }}>
          <FormControl size="small" sx={{ minWidth: 160 }}>
            <InputLabel id="anomaly-type-label">异常类型</InputLabel>
            <Select
              labelId="anomaly-type-label"
              label="异常类型"
              value={type}
              onChange={(event: SelectChangeEvent<string>) => setType(event.target.value as AnomalyType | '')}
            >
              {TYPE_OPTIONS.map((option) => (
                <MenuItem key={option.value || 'all'} value={option.value}>
                  {option.label}
                </MenuItem>
              ))}
            </Select>
          </FormControl>

          <FormControl size="small" sx={{ minWidth: 160 }}>
            <InputLabel id="anomaly-handled-label">处理状态</InputLabel>
            <Select
              labelId="anomaly-handled-label"
              label="处理状态"
              value={handled}
              onChange={(event: SelectChangeEvent<string>) => setHandled(event.target.value)}
            >
              <MenuItem value="0">未处理</MenuItem>
              <MenuItem value="1">已处理</MenuItem>
              <MenuItem value="">全部</MenuItem>
            </Select>
          </FormControl>

          <Box sx={{ flexGrow: 1 }} />
          {result ? (
            <Typography variant="body2" color="text.secondary">
              未处理 {result.summary.unhandled} · 已处理 {result.summary.handled} · 当前筛选命中 {result.total}
            </Typography>
          ) : null}
        </Stack>
      </Paper>

      {error ? (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      ) : null}

      {loading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
          <CircularProgress size={28} />
        </Box>
      ) : null}

      <Stack spacing={1.5}>
        {!loading && result && result.items.length === 0 ? (
          <Paper variant="outlined" sx={{ p: 4, textAlign: 'center' }}>
            <Typography color="text.secondary">当前筛选条件下没有异常记录 🎉</Typography>
          </Paper>
        ) : null}
        {result?.items.map(({ anomaly, question }) => (
          <Box key={anomaly.id}>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
              <StatusChip kind="anomaly" value={anomaly.type} muted={anomaly.handled === 1} />
              <Typography variant="caption" color="text.secondary">
                #{anomaly.id} · {anomaly.detail}
              </Typography>
            </Stack>
            <QuestionCard question={question} />
            <Stack direction="row" spacing={1} sx={{ mt: 0.5 }} justifyContent="flex-end">
              <Button
                size="small"
                variant={anomaly.handled === 1 ? 'outlined' : 'contained'}
                onClick={() => void markHandled(anomaly.id, anomaly.handled !== 1)}
                disabled={busyId === anomaly.id}
              >
                {anomaly.handled === 1 ? '标记为未处理' : '标记已校对'}
              </Button>
            </Stack>
          </Box>
        ))}
      </Stack>

      {result && result.total > PAGE_SIZE ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', mt: 3 }}>
          <Pagination count={totalPages} page={page} onChange={(_e, value) => void load(value)} color="primary" />
        </Box>
      ) : null}
    </Box>
  );
}
