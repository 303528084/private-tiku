/** 导入页：拖拽上传 → 解析结果预览 → 确认入库。 */
import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  LinearProgress,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import { useNavigate } from 'react-router-dom';
import { api, toErrorMessage } from '../api/client';
import type { ImportDetail, ImportRecord } from '../api/client';
import StatusChip from '../components/StatusChip';
import UploadDropzone from '../components/UploadDropzone';
import { MD_TEMPLATE, downloadMarkdownTemplate } from '../constants/import';

interface Feedback {
  severity: 'success' | 'info' | 'warning' | 'error';
  text: string;
}

/** 导入页。 */
export default function ImportPage(): JSX.Element {
  const navigate = useNavigate();
  const [records, setRecords] = useState<ImportRecord[]>([]);
  const [loadingList, setLoadingList] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [detail, setDetail] = useState<ImportDetail | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);

  const loadList = useCallback(async (): Promise<void> => {
    setLoadingList(true);
    try {
      const data = await api.listImports(1, 50);
      setRecords(data.items);
    } catch (caught) {
      setFeedback({ severity: 'error', text: toErrorMessage(caught) });
    } finally {
      setLoadingList(false);
    }
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  const handleFiles = async (files: File[]): Promise<void> => {
    setUploading(true);
    setProgress(0);
    setFeedback(null);
    try {
      const result = await api.uploadFiles(files, setProgress);
      const okFiles = result.files.filter((item) => item.status === 'parsed').length;
      const failed = result.files.length - okFiles;
      setFeedback({
        severity: failed > 0 ? 'warning' : 'success',
        text: `批次 #${result.import_id} 解析完成：成功 ${okFiles} 个文件，失败 ${failed} 个；可预览后确认入库。`,
      });
      await loadList();
    } catch (caught) {
      setFeedback({ severity: 'error', text: toErrorMessage(caught) });
      await loadList();
    } finally {
      setUploading(false);
      setProgress(0);
    }
  };

  const openPreview = async (importId: number): Promise<void> => {
    setBusyId(importId);
    try {
      const data = await api.getImport(importId);
      setDetail(data);
      setDialogOpen(true);
    } catch (caught) {
      setFeedback({ severity: 'error', text: toErrorMessage(caught) });
    } finally {
      setBusyId(null);
    }
  };

  const commit = async (importId: number): Promise<void> => {
    setBusyId(importId);
    try {
      const data = await api.commitImport(importId);
      setFeedback({ severity: 'success', text: `批次 #${importId} 已入库 ${data.committed} 道题。` });
      setDialogOpen(false);
      await loadList();
    } catch (caught) {
      setFeedback({ severity: 'error', text: toErrorMessage(caught) });
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (importId: number): Promise<void> => {
    setBusyId(importId);
    try {
      await api.deleteImport(importId);
      setFeedback({ severity: 'info', text: `批次 #${importId} 已删除。` });
      setDialogOpen(false);
      await loadList();
    } catch (caught) {
      setFeedback({ severity: 'error', text: toErrorMessage(caught) });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Box>
      <Typography variant="h5" sx={{ fontWeight: 700, mb: 0.5 }}>
        导入文档
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        上传后立即同步解析，解析结果先暂存为「待入库」，确认后才会进入检索库。
        v1.3 起支持 <b>结构化 JSON</b> 与 <b>Markdown</b> 两种格式：JSON 多题库包会按题库分列来源，
        一个 Markdown 文件 = 一个题库；系统导出的 md 可原样再导入。
      </Typography>

      <details className="mb-4">
        <summary className="cursor-pointer text-sm text-gray-600 select-none">
          Markdown 写法示例与模板（点击展开）
        </summary>
        <Box sx={{ mt: 1, p: 2, bgcolor: '#fff', border: '1px solid #e6e9f0', borderRadius: 2 }}>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            最少需要一个 <code>## 题号. [题型]</code> 标题；选项写成 <code>- **A.** 内容</code>，
            答案行 <code>**答案**：A</code>，解析行 <code>**解析**：…</code> 可选。
            题号非数字时会按出现顺序自动补号；<code>**来源**：</code> 行会被忽略（来源统一取文件名）。
          </Typography>
          <Box
            component="pre"
            sx={{
              m: 0,
              p: 1.5,
              bgcolor: '#f7f8fb',
              borderRadius: 1,
              fontSize: 12,
              lineHeight: 1.7,
              overflowX: 'auto',
            }}
          >
{MD_TEMPLATE}
          </Box>
          <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
            <Button size="small" variant="outlined" onClick={() => downloadMarkdownTemplate()}>
              下载模板
            </Button>
            <Button size="small" variant="text" onClick={() => navigate('/questions/new')}>
              或者手动录题
            </Button>
          </Stack>
        </Box>
      </details>

      <Paper variant="outlined" sx={{ p: 2, mb: 2, borderRadius: 2 }}>
        <UploadDropzone onFiles={(files) => void handleFiles(files)} disabled={uploading} />
        {uploading ? (
          <Box sx={{ mt: 2 }}>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
              <CircularProgress size={16} />
              <Typography variant="body2" color="text.secondary">
                上传/解析中… {progress > 0 ? `${progress}%` : ''}
              </Typography>
            </Stack>
            <LinearProgress variant={progress > 0 ? 'determinate' : 'indeterminate'} value={progress} />
          </Box>
        ) : null}
        {feedback ? (
          <Alert severity={feedback.severity} sx={{ mt: 2 }} onClose={() => setFeedback(null)}>
            {feedback.text}
          </Alert>
        ) : null}
      </Paper>

      <Paper variant="outlined" sx={{ borderRadius: 2 }}>
        <Box sx={{ px: 2, py: 1.5, display: 'flex', alignItems: 'center', gap: 1 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            导入批次
          </Typography>
          <Chip size="small" label={`${records.length} 条`} />
          <Box sx={{ flexGrow: 1 }} />
          <Button size="small" onClick={() => void loadList()} disabled={loadingList}>
            刷新
          </Button>
          <Button size="small" variant="text" onClick={() => navigate('/stats')}>
            查看统计
          </Button>
        </Box>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>批次</TableCell>
                <TableCell>状态</TableCell>
                <TableCell align="right">文件</TableCell>
                <TableCell align="right">成功/失败</TableCell>
                <TableCell align="right">总题数</TableCell>
                <TableCell align="right">已入库</TableCell>
                <TableCell>创建时间</TableCell>
                <TableCell align="right">操作</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {records.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={8} align="center">
                    {loadingList ? '加载中…' : '暂无导入记录，先上传一份题库文档吧'}
                  </TableCell>
                </TableRow>
              ) : null}
              {records.map((record) => (
                <TableRow key={record.id} hover>
                  <TableCell>#{record.id}</TableCell>
                  <TableCell>
                    <StatusChip kind="import_status" value={record.status} />
                  </TableCell>
                  <TableCell align="right">{record.total_files}</TableCell>
                  <TableCell align="right">
                    {record.ok_files} / {record.failed_files}
                  </TableCell>
                  <TableCell align="right">{record.total_questions}</TableCell>
                  <TableCell align="right">{record.committed_questions}</TableCell>
                  <TableCell>{record.created_at}</TableCell>
                  <TableCell align="right">
                    <Stack direction="row" spacing={0.5} justifyContent="flex-end">
                      <Button size="small" onClick={() => void openPreview(record.id)} disabled={busyId === record.id}>
                        预览
                      </Button>
                      <Button
                        size="small"
                        variant="contained"
                        onClick={() => void commit(record.id)}
                        disabled={busyId === record.id || record.total_questions === 0}
                      >
                        入库
                      </Button>
                      <Button
                        size="small"
                        color="error"
                        onClick={() => void remove(record.id)}
                        disabled={busyId === record.id}
                      >
                        删除
                      </Button>
                    </Stack>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>批次 #{detail?.import.id} 解析明细</DialogTitle>
        <DialogContent dividers>
          {detail ? (
            <Box>
              <Stack direction="row" spacing={1} sx={{ mb: 2 }} flexWrap="wrap" useFlexGap>
                <StatusChip kind="import_status" value={detail.import.status} />
                <Chip size="small" label={`文件 ${detail.import.total_files}`} />
                <Chip size="small" label={`题目 ${detail.import.total_questions}`} />
                <Chip size="small" label={`已入库 ${detail.import.committed_questions}`} />
              </Stack>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>文件名</TableCell>
                    <TableCell>类型</TableCell>
                    <TableCell>状态</TableCell>
                    <TableCell align="right">题数</TableCell>
                    <TableCell align="right">已入库</TableCell>
                    <TableCell align="right">未处理异常</TableCell>
                    <TableCell align="right">异常总数</TableCell>
                    <TableCell>说明</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {detail.files.map((file) => (
                    <TableRow key={file.file_id}>
                      <TableCell>{file.filename}</TableCell>
                      <TableCell>{file.file_type ?? '-'}</TableCell>
                      <TableCell>
                        <StatusChip kind="file_status" value={file.status} />
                      </TableCell>
                      <TableCell align="right">{file.total_questions}</TableCell>
                      <TableCell align="right">{file.committed_questions ?? '-'}</TableCell>
                      <TableCell align="right">{file.anomaly_count}</TableCell>
                      <TableCell align="right">{file.anomaly_total ?? '-'}</TableCell>
                      <TableCell>{file.error || '-'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          ) : (
            <Typography variant="body2">加载中…</Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button color="error" onClick={() => detail && void remove(detail.import.id)}>
            删除批次
          </Button>
          <Box sx={{ flexGrow: 1 }} />
          <Button onClick={() => setDialogOpen(false)}>关闭</Button>
          <Button
            variant="contained"
            onClick={() => detail && void commit(detail.import.id)}
            disabled={!detail || detail.import.total_questions === 0}
          >
            全部入库
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
