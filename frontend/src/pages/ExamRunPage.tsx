/**
 * 作答页：一场练习/模拟考试的作答与结果回看（/practice/:examId）。
 *
 * 三种形态：
 * 1. 练习进行中：逐题作答，「提交答案」立刻判分并显示正确答案与解析；
 * 2. 考试进行中：选项点选即静默提交，作答期间不显示答案，右上角「交卷」统一判分；
 * 3. 已交卷：得分汇总 + 逐题回看（我的答案 / 正确答案 / 解析 / 对错）。
 *
 * 数据全部来自后端快照（exam_answers），原题被删除后照常作答与回看。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  LinearProgress,
  Paper,
  Stack,
  Typography,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import ArrowForwardIcon from '@mui/icons-material/ArrowForward';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import CancelIcon from '@mui/icons-material/Cancel';
import SendIcon from '@mui/icons-material/Send';
import { api, toErrorMessage } from '../api/client';
import type { ExamDetail, ExamItem } from '../api/client';
import AnswerPicker from '../components/AnswerPicker';
import PageHeader from '../components/PageHeader';
import StatusChip from '../components/StatusChip';

const MODE_LABEL: Record<string, string> = {
  practice: '练习',
  exam: '模拟考试',
};

/** 题干/选项/答案的纯文本展示（无高亮需求）。 */
function OptionList({ item }: { item: ExamItem }): JSX.Element | null {
  const letters = Object.keys(item.options).sort();
  if (letters.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
        无选项（判断题）
      </Typography>
    );
  }
  return (
    <div className="option-grid mt-2">
      {letters.map((letter) => (
        <Typography component="div" variant="body2" key={letter} className="option-row">
          <span className="option-letter">{letter}.</span>
          <span className="option-text">{item.options[letter]}</span>
        </Typography>
      ))}
    </div>
  );
}

/** 作答页。 */
export default function ExamRunPage(): JSX.Element {
  const { examId } = useParams<{ examId: string }>();
  const navigate = useNavigate();

  const id = Number.parseInt(examId ?? '', 10);
  const [detail, setDetail] = useState<ExamDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');

  /** 当前展示第几题（items 下标，0 起）。 */
  const [index, setIndex] = useState(0);
  /** 草稿答案：seq → 当前点选的答案（提交前/考试模式下的本地状态）。 */
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [finishOpen, setFinishOpen] = useState(false);
  const [finishing, setFinishing] = useState(false);

  const load = useCallback(
    async (targetId: number, keepIndex = false): Promise<void> => {
      setLoading(true);
      setError('');
      try {
        const data = await api.getExam(targetId);
        setDetail(data);
        if (!keepIndex) {
          setIndex(0);
        }
        // 已作答的题用已提交的答案初始化草稿
        const seeded: Record<number, string> = {};
        data.items.forEach((item) => {
          if (item.answered && item.answer_given) {
            seeded[item.seq] = item.answer_given;
          }
        });
        setDrafts((previous) => ({ ...seeded, ...previous }));
      } catch (caught) {
        setError(toErrorMessage(caught));
        setDetail(null);
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (!Number.isNaN(id)) {
      void load(id);
    } else {
      setError('试卷 id 非法');
      setLoading(false);
    }
  }, [id, load]);

  const items = detail?.items ?? [];
  const exam = detail?.exam ?? null;
  const current = items[index] ?? null;
  const finished = exam?.status === 'finished';
  const isPractice = exam?.mode === 'practice';
  const answeredCount = items.filter((item) => item.answered).length;
  const draftAnswer = current ? drafts[current.seq] ?? '' : '';

  const progress = items.length > 0 ? (answeredCount / items.length) * 100 : 0;

  /** 草稿答案变化。 */
  const handleDraftChange = (item: ExamItem, next: string): void => {
    setDrafts((previous) => ({ ...previous, [item.seq]: next }));
    if (!isPractice && !finished) {
      // 考试模式：点选即静默提交（后端记录作答，交卷时统一判分）
      api
        .submitExamAnswer(item.exam_id, item.seq, next)
        .then((updated) => {
          setDetail((previous) => {
            if (!previous) {
              return previous;
            }
            return {
              ...previous,
              items: previous.items.map((entry) => (entry.seq === updated.seq ? updated : entry)),
            };
          });
        })
        .catch((caught: unknown) => {
          setActionError(toErrorMessage(caught));
        });
    }
  };

  /** 练习模式：提交当前题并即时判分。 */
  const handlePracticeSubmit = async (): Promise<void> => {
    if (!current || !draftAnswer) {
      return;
    }
    setSubmitting(true);
    setActionError('');
    try {
      const updated = await api.submitExamAnswer(current.exam_id, current.seq, draftAnswer);
      setDetail((previous) => {
        if (!previous) {
          return previous;
        }
        return {
          ...previous,
          items: previous.items.map((entry) => (entry.seq === updated.seq ? updated : entry)),
        };
      });
    } catch (caught) {
      setActionError(toErrorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  };

  /** 交卷（练习 / 考试通用）：未答题按错计。 */
  const handleFinish = async (): Promise<void> => {
    if (!exam) {
      return;
    }
    setFinishing(true);
    setActionError('');
    try {
      await api.finishExam(exam.id);
      setFinishOpen(false);
      await load(exam.id, true);
    } catch (caught) {
      setActionError(toErrorMessage(caught));
      setFinishOpen(false);
    } finally {
      setFinishing(false);
    }
  };

  const unansweredCount = useMemo(
    () => items.filter((item) => !item.answered).length,
    [items],
  );

  if (loading) {
    return (
      <Typography variant="body2" color="text.secondary" sx={{ py: 6, textAlign: 'center' }}>
        加载中…
      </Typography>
    );
  }

  if (error || !exam || detail === null) {
    return (
      <Box>
        <Alert severity="error" sx={{ mb: 2 }}>
          {error || '试卷不存在'}
        </Alert>
        <Button variant="outlined" startIcon={<ArrowBackIcon />} onClick={() => navigate('/practice')}>
          返回刷题首页
        </Button>
      </Box>
    );
  }

  // =========================================================================
  // 已交卷：结果回看
  // =========================================================================
  if (finished) {
    return (
      <Box>
        <PageHeader
          title={`${MODE_LABEL[exam.mode] ?? exam.mode}结果`}
          description={`${exam.created_at} · 共 ${exam.question_count} 题`}
          actions={
            <Button variant="outlined" startIcon={<ArrowBackIcon />} onClick={() => navigate('/practice')}>
              返回
            </Button>
          }
        />
        <Paper variant="outlined" sx={{ borderRadius: 2, p: 3, mb: 3, textAlign: 'center' }}>
          <Typography variant="body2" color="text.secondary">
            得分
          </Typography>
          <Typography variant="h2" sx={{ fontWeight: 700, color: exam.score >= 60 ? 'success.main' : 'error.main' }}>
            {exam.score}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            答对 {exam.correct_count} / {exam.question_count} 题
            {isPractice ? '' : ` · 作答 ${exam.answered_count} 题，未答按错计`}
          </Typography>
        </Paper>

        <Stack spacing={2}>
          {items.map((item) => {
            const correct = item.is_correct === true;
            const wrong = item.is_correct === false;
            return (
              <Paper
                key={item.id}
                variant="outlined"
                sx={{
                  borderRadius: 2,
                  p: 2.5,
                  borderLeft: 4,
                  borderLeftColor: correct ? 'success.main' : wrong ? 'error.main' : 'grey.300',
                }}
              >
                <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                  <StatusChip kind="qtype" value={item.qtype} />
                  <Typography variant="caption" color="text.secondary">
                    第 {item.seq} 题
                  </Typography>
                  {correct ? (
                    <Stack direction="row" spacing={0.5} alignItems="center">
                      <CheckCircleIcon sx={{ fontSize: 16 }} color="success" />
                      <Typography variant="caption" color="success.main" sx={{ fontWeight: 600 }}>
                        答对
                      </Typography>
                    </Stack>
                  ) : wrong ? (
                    <Stack direction="row" spacing={0.5} alignItems="center">
                      <CancelIcon sx={{ fontSize: 16 }} color="error" />
                      <Typography variant="caption" color="error.main" sx={{ fontWeight: 600 }}>
                        {item.answered ? '答错' : '未答'}
                      </Typography>
                    </Stack>
                  ) : null}
                </Stack>

                <Typography variant="subtitle1" sx={{ fontWeight: 600, lineHeight: 1.6, mt: 1.5 }}>
                  {item.stem}
                </Typography>
                <OptionList item={item} />

                <Divider sx={{ my: 1.5 }} />
                <Stack spacing={0.5}>
                  <Typography variant="body2" color={wrong ? 'error.main' : 'text.primary'}>
                    我的答案：{item.answer_given || '（未作答）'}
                  </Typography>
                  <Typography variant="body2" color="success.main">
                    正确答案：{item.answer || '未识别'}
                  </Typography>
                  {item.analysis ? (
                    <Typography variant="body2" color="text.secondary">
                      解析：{item.analysis}
                    </Typography>
                  ) : null}
                </Stack>
              </Paper>
            );
          })}
        </Stack>
      </Box>
    );
  }

  // =========================================================================
  // 进行中：作答
  // =========================================================================
  const showFeedback = isPractice && current !== null && current.answered;
  const lastQuestion = index === items.length - 1;

  return (
    <Box>
      <PageHeader
        title={MODE_LABEL[exam.mode] ?? exam.mode}
        description={`第 ${index + 1} / ${items.length} 题 · 已答 ${answeredCount} 题`}
        actions={
          <Stack direction="row" spacing={1}>
            <Button variant="outlined" startIcon={<ArrowBackIcon />} onClick={() => navigate('/practice')}>
              退出
            </Button>
            <Button
              variant="contained"
              color={isPractice ? 'primary' : 'error'}
              startIcon={<SendIcon />}
              onClick={() => setFinishOpen(true)}
            >
              {isPractice ? '完成练习' : '交卷'}
            </Button>
          </Stack>
        }
      />

      <LinearProgress
        variant="determinate"
        value={progress}
        sx={{ borderRadius: 1, height: 6, mb: 2 }}
        aria-label="作答进度"
      />

      {actionError ? (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setActionError('')}>
          {actionError}
        </Alert>
      ) : null}

      {current !== null ? (
        <Paper variant="outlined" sx={{ borderRadius: 2, p: 2.5 }}>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
            <StatusChip kind="qtype" value={current.qtype} />
            <Typography variant="caption" color="text.secondary">
              第 {current.seq} 题
            </Typography>
            {current.answered ? (
              <Typography variant="caption" color="text.secondary">
                已作答
              </Typography>
            ) : null}
          </Stack>

          <Typography variant="subtitle1" sx={{ fontWeight: 600, lineHeight: 1.6, mt: 1.5 }}>
            {current.stem}
          </Typography>
          <OptionList item={current} />

          <Box sx={{ mt: 2 }}>
            <AnswerPicker
              qtype={current.qtype}
              answer={draftAnswer}
              options={current.options}
              onChange={(next) => handleDraftChange(current, next)}
              disabled={submitting || (isPractice && current.answered)}
            />
          </Box>

          {isPractice ? (
            <Box sx={{ mt: 2 }}>
              <Button
                variant="contained"
                disabled={submitting || !draftAnswer || current.answered}
                onClick={() => void handlePracticeSubmit()}
              >
                {current.answered ? '已提交' : submitting ? '提交中…' : '提交答案'}
              </Button>
            </Box>
          ) : null}

          {showFeedback ? (
            <Box sx={{ mt: 2 }}>
              {current.is_correct === true ? (
                <Alert severity="success" icon={<CheckCircleIcon fontSize="inherit" />}>
                  回答正确！
                </Alert>
              ) : (
                <Alert severity="error" icon={<CancelIcon fontSize="inherit" />}>
                  回答错误。正确答案：{current.answer || '未识别'}
                </Alert>
              )}
              {current.analysis ? (
                <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                  解析：{current.analysis}
                </Typography>
              ) : null}
            </Box>
          ) : null}
        </Paper>
      ) : (
        <Alert severity="info">这场试卷没有题目。</Alert>
      )}

      <Stack direction="row" justifyContent="space-between" sx={{ mt: 2 }}>
        <Button
          variant="outlined"
          startIcon={<ArrowBackIcon />}
          disabled={index === 0}
          onClick={() => setIndex((previous) => Math.max(0, previous - 1))}
        >
          上一题
        </Button>
        <Button
          variant="outlined"
          endIcon={<ArrowForwardIcon />}
          disabled={lastQuestion}
          onClick={() => setIndex((previous) => Math.min(items.length - 1, previous + 1))}
        >
          下一题
        </Button>
      </Stack>

      {/* ---- 交卷 / 完成练习确认 ---- */}
      <Dialog open={finishOpen} onClose={() => setFinishOpen(false)}>
        <DialogTitle>{isPractice ? '完成本次练习？' : '确认交卷？'}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            共 {items.length} 题，已答 {answeredCount} 题。
            {unansweredCount > 0
              ? `还有 ${unansweredCount} 题未作答，${isPractice ? '未答题' : '未答题'}将按错误计入成绩。`
              : '全部题目均已作答。'}
            {isPractice ? '' : '交卷后立即判分，不能再修改答案。'}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setFinishOpen(false)}>继续作答</Button>
          <Button variant="contained" color={isPractice ? 'primary' : 'error'} disabled={finishing} onClick={() => void handleFinish()}>
            {finishing ? '判分中…' : isPractice ? '完成练习' : '确认交卷'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
