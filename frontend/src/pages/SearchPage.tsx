/** 首页：全文搜索 + 筛选 + 结果高亮。 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Pagination,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import { useNavigate } from 'react-router-dom';
import { api, toErrorMessage } from '../api/client';
import type { SearchResult } from '../api/client';
import FilterBar, { EMPTY_FILTERS } from '../components/FilterBar';
import type { DocumentOption, SearchFilters } from '../components/FilterBar';
import QuestionCard from '../components/QuestionCard';

const PAGE_SIZE = 10;

/** 搜索页。 */
export default function SearchPage(): JSX.Element {
  const navigate = useNavigate();
  const [keyword, setKeyword] = useState('');
  const [filters, setFilters] = useState<SearchFilters>(EMPTY_FILTERS);
  const [result, setResult] = useState<SearchResult | null>(null);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [documents, setDocuments] = useState<DocumentOption[]>([]);

  const runSearch = useCallback(
    async (nextPage: number): Promise<void> => {
      setLoading(true);
      setError('');
      try {
        const data = await api.search({
          q: keyword.trim(),
          type: filters.qtype,
          only_anomaly: filters.onlyAnomaly,
          doc_id: filters.docId ? Number(filters.docId) : null,
          page: nextPage,
          size: PAGE_SIZE,
        });
        setResult(data);
        setPage(nextPage);
        setDocuments((previous) => {
          const merged = new Map<number, string>(previous.map((item) => [item.id, item.name]));
          data.items.forEach((item) => {
            if (item.filename) {
              merged.set(item.import_file_id, item.filename);
            }
          });
          return Array.from(merged.entries()).map(([id, name]) => ({ id, name }));
        });
      } catch (caught) {
        setError(toErrorMessage(caught));
        setResult(null);
      } finally {
        setLoading(false);
      }
    },
    [keyword, filters],
  );

  useEffect(() => {
    void runSearch(1);
    // 首次进入自动列出全部已入库题目
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const totalPages = useMemo(() => {
    if (!result || result.total === 0) {
      return 1;
    }
    return Math.max(1, Math.ceil(result.total / PAGE_SIZE));
  }, [result]);

  return (
    <Box>
      <Typography variant="h5" sx={{ fontWeight: 700, mb: 0.5 }}>
        搜索题库
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        输入关键词（空格分隔多关键词），可检索题干、选项、答案与解析。
      </Typography>

      <Paper variant="outlined" sx={{ p: 2, mb: 2, borderRadius: 2 }}>
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5} sx={{ mb: 2 }}>
          <TextField
            fullWidth
            size="small"
            label="关键词，例如：房颤 脉搏短绌"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                void runSearch(1);
              }
            }}
          />
          <Button
            variant="contained"
            startIcon={<SearchIcon />}
            onClick={() => void runSearch(1)}
            disabled={loading}
            sx={{ minWidth: 120 }}
          >
            搜索
          </Button>
          <Button
            variant="outlined"
            onClick={() => {
              setKeyword('');
              setFilters(EMPTY_FILTERS);
              navigate('/');
            }}
          >
            重置
          </Button>
        </Stack>
        <FilterBar value={filters} onChange={setFilters} documents={documents} disabled={loading} />
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

      {!loading && result ? (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
          共 {result.total} 条结果 · 耗时 {result.took_ms} ms
          {result.total === 0 ? '（未命中，试试「房颤」「脉搏短绌」等关键词）' : ''}
        </Typography>
      ) : null}

      <Stack spacing={1.5}>
        {result?.items.map((item) => (
          <QuestionCard key={item.id} question={item} highlight={item.highlight} />
        ))}
      </Stack>

      {result && result.total > PAGE_SIZE ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', mt: 3 }}>
          <Pagination
            count={totalPages}
            page={page}
            onChange={(_event, value) => void runSearch(value)}
            color="primary"
          />
        </Box>
      ) : null}
    </Box>
  );
}
