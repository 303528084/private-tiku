/** 统计页：题库总量 / 异常数 / 导入批次 / 导出入口。 */
import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import { Link as RouterLink } from 'react-router-dom';
import { api, toErrorMessage } from '../api/client';
import type { ImportRecord, StatsResult } from '../api/client';
import PageHeader from '../components/PageHeader';
import StatusChip from '../components/StatusChip';

interface StatCard {
  label: string;
  value: number;
  hint: string;
  color: string;
}

/** 统计页。 */
export default function StatsPage(): JSX.Element {
  const [stats, setStats] = useState<StatsResult | null>(null);
  const [records, setRecords] = useState<ImportRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError('');
    try {
      const [statsData, importData] = await Promise.all([api.stats(), api.listImports(1, 50)]);
      setStats(statsData);
      setRecords(importData.items);
    } catch (caught) {
      setError(toErrorMessage(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const cards: StatCard[] = stats
    ? [
        { label: '已入库题目', value: stats.total_published, hint: '可被检索', color: '#1f6feb' },
        { label: '待入库题目', value: stats.total_pending, hint: '需确认后入库', color: '#ed6c02' },
        { label: '未处理异常', value: stats.anomaly_open, hint: '待校对', color: '#d32f2f' },
        { label: '导入批次', value: stats.total_imports, hint: `文件 ${stats.total_files} 个`, color: '#2e7d32' },
      ]
    : [];

  return (
    <Box>
      <PageHeader
        title="导入统计"
        description="题库总体规模、异常概况与按批次的导入明细。"
        actions={
          <>
            <Button
              variant="outlined"
              startIcon={<DownloadIcon />}
              href={api.exportUrl('md')}
              sx={{ mr: 1 }}
            >
              导出 Markdown
            </Button>
            <Button variant="contained" startIcon={<DownloadIcon />} href={api.exportUrl('json')}>
              导出 JSON
            </Button>
          </>
        }
      />

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

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, 1fr)', md: 'repeat(4, 1fr)' },
          gap: 2,
          mb: 2,
        }}
      >
        {cards.map((card) => (
          <Card key={card.label} variant="outlined" sx={{ borderRadius: 2 }}>
            <CardContent>
              <Typography variant="body2" color="text.secondary">
                {card.label}
              </Typography>
              <Typography variant="h4" sx={{ fontWeight: 700, color: card.color }}>
                {card.value}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {card.hint}
              </Typography>
            </CardContent>
          </Card>
        ))}
      </Box>

      <Paper variant="outlined" sx={{ borderRadius: 2 }}>
        <Box sx={{ px: 2, py: 1.5, display: 'flex', alignItems: 'center' }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, flexGrow: 1 }}>
            批次明细
          </Typography>
          <Button component={RouterLink} to="/import" size="small">
            去导入
          </Button>
          <Button size="small" onClick={() => void load()}>
            刷新
          </Button>
        </Box>
        <TableContainer sx={{ overflowX: 'auto' }}>
          <Table size="small" sx={{ minWidth: 760 }}>
            <TableHead>
              <TableRow>
                <TableCell>批次</TableCell>
                <TableCell>状态</TableCell>
                <TableCell align="right">文件</TableCell>
                <TableCell align="right">成功/失败</TableCell>
                <TableCell align="right">题数</TableCell>
                <TableCell align="right">未处理异常</TableCell>
                <TableCell align="right">已入库</TableCell>
                <TableCell>时间</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {records.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={8} align="center">
                    暂无导入批次
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
                  <TableCell align="right">{record.anomaly_count ?? '-'}</TableCell>
                  <TableCell align="right">{record.committed_questions}</TableCell>
                  <TableCell>{record.created_at}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>
    </Box>
  );
}
