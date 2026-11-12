/** 搜索筛选条：题型 / 仅看有异常 / 来源文档。 */
import {
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Switch,
  TextField,
} from '@mui/material';
import type { SelectChangeEvent } from '@mui/material';
import type { QType } from '../api/client';

export interface SearchFilters {
  qtype: QType | '';
  onlyAnomaly: boolean;
  /** 以字符串保存便于 Select 绑定，'' 表示不限。 */
  docId: string;
}

export const EMPTY_FILTERS: SearchFilters = { qtype: '', onlyAnomaly: false, docId: '' };

export interface DocumentOption {
  id: number;
  name: string;
}

export interface FilterBarProps {
  value: SearchFilters;
  onChange: (next: SearchFilters) => void;
  documents: DocumentOption[];
  disabled?: boolean;
}

const QTYPE_OPTIONS: Array<{ value: QType | ''; label: string }> = [
  { value: '', label: '全部题型' },
  { value: 'single', label: '单选题' },
  { value: 'multiple', label: '多选题' },
  { value: 'judge', label: '判断题' },
  { value: 'unknown', label: '未知题型' },
];

/** 筛选条组件。 */
export default function FilterBar({
  value,
  onChange,
  documents,
  disabled = false,
}: FilterBarProps): JSX.Element {
  const handleQType = (event: SelectChangeEvent<string>): void => {
    onChange({ ...value, qtype: event.target.value as QType | '' });
  };

  return (
    <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} alignItems={{ md: 'center' }}>
      <FormControl size="small" sx={{ minWidth: 140 }} disabled={disabled}>
        <InputLabel id="filter-qtype-label">题型</InputLabel>
        <Select
          labelId="filter-qtype-label"
          label="题型"
          value={value.qtype}
          onChange={handleQType}
        >
          {QTYPE_OPTIONS.map((option) => (
            <MenuItem key={option.value || 'all'} value={option.value}>
              {option.label}
            </MenuItem>
          ))}
        </Select>
      </FormControl>

      <FormControl size="small" sx={{ minWidth: 220 }} disabled={disabled}>
        <TextField
          select
          size="small"
          label="来源文档"
          value={value.docId}
          onChange={(event) => onChange({ ...value, docId: event.target.value })}
        >
          <MenuItem value="">全部文档</MenuItem>
          {documents.map((doc) => (
            <MenuItem key={doc.id} value={String(doc.id)}>
              {doc.name}
            </MenuItem>
          ))}
        </TextField>
      </FormControl>

      <FormControlLabel
        control={
          <Switch
            size="small"
            checked={value.onlyAnomaly}
            onChange={(event) => onChange({ ...value, onlyAnomaly: event.target.checked })}
            disabled={disabled}
          />
        }
        label="仅看有异常"
      />
    </Stack>
  );
}
