/**
 * 刷题 / 模拟考试首页：开考配置（模式、题库范围、题数、题型）+ 历史记录。
 *
 * 开考 → POST /api/exams（随机抽题落快照）→ 跳 /practice/:examId 作答。
 * 历史行点击「继续作答 / 查看结果」进入同一详情路由。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControlLabel,
  FormGroup,
  IconButton,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import RefreshIcon from '@mui/icons-material/Refresh';
import { api, toErrorMessage } from '../api/client';
import type {
  ExamCreatePayload,
  ExamListResult,
  ExamMode,
  ExamRecord,
  LibraryItem,
  QType,
} from '../api/client';
import PageHeader from '../components/PageHeader';

const QTYPE_OPTIONS: Array<{ value: QType; label: string }> = [
  { value: 'single', label: '单选' },
  { value: 'multiple', label: '多选' },
  { value: 'judge', label: '判断' },
];

const MODE_META: Record<ExamMode, { label: string }> = {
  practice: { label: '练习' },
  exam: { label: '模拟考试' },
};

/** 历史记录行。 */
function modeChip(mode: ExamMode): JSX.Element {
  return (
    <Chip
      size="small"
      label={MODE_META[mode]?.label ?? mode}
      color={mode === 'exam' ? 'secondary' : 'primary'}
      variant="outlined"
      sx={{ fontSize: 12, height: 22 }}
    />
  );
}

/** 刷题 / 模拟考试首页。 */
export default function PracticeHomePage(): JSX.Element {
  const navigate = useNavigate();

  // ---- 开考配置 ----
  const [mode, setMode] = useState<ExamMode>('practice');
  const [count, setCount] = useState('20');
  const [qtypes, setQtypes] = useState<QType[]>([]);
  const [libraries, setLibraries] = useState<LibraryItem[]>([]);
  const [selectedLibraries, setSelectedLibraries] = useState<LibraryItem[]>([]);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState('');

  // ---- 历史记录 ----
  const [history, setHistory] = useState<ExamListResult | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<ExamRecord | null>(null);
  const [deleting, setDeleting] = useState(false);

  const loadHistory = useCallback(async (): Promise<void> => {
    setHistoryLoading(true);
    setHistoryError('');
    try {
      const data = await api.listExams({ page: 1, size: 50 });
      setHistory(data);
    } catch (caught) {
      setHistoryError(toErrorMessage(caught));
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    api
      .listLibraries()
      .then((data) => {
        if (active) {
          setLibraries(data.items);
        }
      })
      .catch(() => {
        // 题库列表加载失败不阻塞开考：范围为空即「全部题库」
      });
    void loadHistory();
    return () => {
      active = false;
    };
  }, [loadHistory]);

  const countValue = useMemo(() => {
    const parsed = Number.parseInt(count, 10);
    if (Number.isNaN(parsed)) {
      return 0;
    }
    return parsed;
  }, [count]);

  const countInvalid = countValue < 1 || countValue > 200;

  const handleStart = async (): Promise<void> => {
    setStartError('');
    const payload: ExamCreatePayload = {
      mode,
      count: countValue,
      library_ids: selectedLibraries.map((item) => item.id),
      qtypes,
    };
    setStarting(true);
    try {
      const exam = await api.createExam(payload);
      navigate(`/practice/${exam.id}`);
    } catch (caught) {
      setStartError(toErrorMessage(caught));
      setStarting(false);
    }
  };

  const handleDelete = async (): Promise<void> => {
    if (!deleteTarget) {
      return;
    }
    setDeleting(true);
    try {
      await api.deleteExam(deleteTarget.id);
      setDeleteTarget(null);
      await loadHistory();
    } catch (caught) {
      setHistoryError(toErrorMessage(caught));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <Box>
      <PageHeader
        title="刷题 / 模拟考试"
        description="练习模式答一题判一题；模拟考试作答期间不给答案，交卷后统一判分。"
      />

      {/* ---- 开考配置 ---- */}
      <Paper variant="outlined" sx={{ borderRadius: 2, p: 2.5, mb: 3 }}>
        <Stack spacing={2.5}>
          <Box>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 0.5 }}>
              模式
            </Typography>
            <ToggleButtonGroup
              exclusive
              value={mode}
              onChange={(_event, next: ExamMode | null) => {
                if (next) {
                  setMode(next);
                }
              }}
              size="small"
            >
              <ToggleButton value="practice">练习模式</ToggleButton>
              <ToggleButton value="exam">模拟考试</ToggleButton>
            </ToggleButtonGroup>
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5 }}>
              {mode === 'practice'
                ? '逐题提交，立刻显示对错、正确答案与解析。'
                : '作答期间不显示答案；交卷后统一判分（未答题按错计）。'}
            </Typography>
          </Box>

          <Box>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 0.5 }}>
              题数（1–200；题库不足时按实际可用题数开考）
            </Typography>
            <TextField
              size="small"
              value={count}
              onChange={(event) => setCount(event.target.value.replace(/[^0-9]/g, ''))}
              error={countInvalid}
              sx={{ width: 140 }}
              inputProps={{ inputMode: 'numeric', 'aria-label': '题数' }}
              helperText={countInvalid ? '请输入 1–200' : undefined}
              FormHelperTextProps={{ component: 'div' }}
            />
          </Box>

          <Box>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 0.5 }}>
              题型（不选 = 不限）
            </Typography>
            <FormGroup row>
              {QTYPE_OPTIONS.map((option) => (
                <FormControlLabel
                  key={option.value}
                  control={
                    <Checkbox
                      size="small"
                      checked={qtypes.indexOf(option.value) >= 0}
                      onChange={(_event, checked: boolean) => {
                        setQtypes((previous) =>
                          checked
                            ? [...previous, option.value]
                            : previous.filter((item) => item !== option.value),
                        );
                      }}
                    />
                  }
                  label={<Typography variant="body2">{option.label}</Typography>}
                />
              ))}
            </FormGroup>
          </Box>

          <Box>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 0.5 }}>
              题库范围（不选 = 全部题库）
            </Typography>
            <Autocomplete
              multiple
              size="small"
              options={libraries}
              getOptionLabel={(option) => option.name}
              value={selectedLibraries}
              onChange={(_event, next: LibraryItem[]) => setSelectedLibraries(next)}
              isOptionEqualToValue={(option, value) => option.id === value.id}
              renderOption={(props, option) => (
                <li {...props} key={`${option.id}`}>
                  <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                    <Typography variant="body2" noWrap>
                      {option.name}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {option.question_count} 题
                    </Typography>
                  </Box>
                </li>
              )}
              renderInput={(params) => (
                <TextField {...params} placeholder="搜索题库" variant="outlined" />
              )}
              sx={{ maxWidth: 520 }}
            />
          </Box>

          {startError ? <Alert severity="error">{startError}</Alert> : null}

          <Box>
            <Button
              variant="contained"
              size="large"
              startIcon={<PlayArrowIcon />}
              disabled={starting || countInvalid}
              onClick={() => void handleStart()}
            >
              {starting ? '抽题中…' : mode === 'practice' ? '开始练习' : '开始考试'}
            </Button>
          </Box>
        </Stack>
      </Paper>

      {/* ---- 历史记录 ---- */}
      <Paper variant="outlined" sx={{ borderRadius: 2 }}>
        <Box sx={{ px: 2, py: 1.5, display: 'flex', alignItems: 'center' }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, flexGrow: 1 }}>
            历史记录
          </Typography>
          <IconButton size="small" aria-label="刷新历史" onClick={() => void loadHistory()}>
            <RefreshIcon fontSize="small" />
          </IconButton>
        </Box>
        {historyError ? (
          <Alert severity="error" sx={{ mx: 2, mb: 2 }}>
            {historyError}
          </Alert>
        ) : null}
        {historyLoading ? (
          <Typography variant="body2" color="text.secondary" sx={{ px: 2, pb: 2 }}>
            加载中…
          </Typography>
        ) : (
          <TableContainer sx={{ overflowX: 'auto' }}>
            <Table size="small" sx={{ minWidth: 720 }}>
              <TableHead>
                <TableRow>
                  <TableCell>时间</TableCell>
                  <TableCell>模式</TableCell>
                  <TableCell align="right">题数</TableCell>
                  <TableCell align="right">已答</TableCell>
                  <TableCell align="right">答对</TableCell>
                  <TableCell align="right">得分</TableCell>
                  <TableCell>状态</TableCell>
                  <TableCell align="right">操作</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {!history || history.items.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={8} align="center">
                      暂无记录，从上方开一场吧
                    </TableCell>
                  </TableRow>
                ) : null}
                {history && history.items
                  ? history.items.map((record) => (
                      <TableRow key={record.id} hover>
                        <TableCell>{record.created_at}</TableCell>
                        <TableCell>{modeChip(record.mode)}</TableCell>
                        <TableCell align="right">{record.question_count}</TableCell>
                        <TableCell align="right">{record.answered_count}</TableCell>
                        <TableCell align="right">{record.correct_count}</TableCell>
                        <TableCell align="right">
                          {record.status === 'finished' ? record.score : '—'}
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" color={record.status === 'finished' ? 'text.secondary' : 'warning.main'}>
                            {record.status === 'finished' ? '已完成' : '进行中'}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">
                          <Button
                            size="small"
                            variant={record.status === 'finished' ? 'outlined' : 'contained'}
                            onClick={() => navigate(`/practice/${record.id}`)}
                            sx={{ mr: 1 }}
                          >
                            {record.status === 'finished' ? '查看结果' : '继续作答'}
                          </Button>
                          <Tooltip title="删除记录">
                            <IconButton
                              size="small"
                              aria-label={`删除记录 ${record.id}`}
                              onClick={() => setDeleteTarget(record)}
                            >
                              <DeleteOutlineIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        </TableCell>
                      </TableRow>
                    ))
                  : null}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      {/* ---- 删除确认 ---- */}
      <Dialog open={deleteTarget !== null} onClose={() => setDeleteTarget(null)}>
        <DialogTitle>删除这条记录？</DialogTitle>
        <DialogContent>
          <DialogContentText>
            将删除{deleteTarget ? MODE_META[deleteTarget.mode]?.label : ''}记录
            {deleteTarget ? `（${deleteTarget.created_at}，${deleteTarget.question_count} 题）` : ''}
            及其全部作答明细，删除后不可恢复。
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteTarget(null)}>取消</Button>
          <Button color="error" disabled={deleting} onClick={() => void handleDelete()}>
            {deleting ? '删除中…' : '删除'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
