/** 状态/题型/异常类型彩色徽标。 */
import { Chip } from '@mui/material';
import type { ChipProps } from '@mui/material';
import type { AnomalyType, ImportStatus, QType, QuestionStatus } from '../api/client';

export type ChipKind = 'qtype' | 'anomaly' | 'question_status' | 'import_status' | 'file_status';

export interface StatusChipProps {
  kind: ChipKind;
  value: string;
  size?: ChipProps['size'];
  /** 异常已处理时使用灰色描边。 */
  muted?: boolean;
}

const QTYPE_META: Record<QType, { label: string; color: ChipProps['color'] }> = {
  single: { label: '单选', color: 'primary' },
  multiple: { label: '多选', color: 'secondary' },
  judge: { label: '判断', color: 'info' },
  unknown: { label: '未知', color: 'default' },
};

const ANOMALY_META: Record<AnomalyType, { label: string; color: ChipProps['color'] }> = {
  missing_answer: { label: '缺答案', color: 'error' },
  missing_options: { label: '缺选项', color: 'error' },
  too_few_options: { label: '选项不足', color: 'warning' },
  empty_stem: { label: '无题干', color: 'error' },
  duplicate_number: { label: '题号重复', color: 'warning' },
  parse_error: { label: '解析异常', color: 'error' },
};

const QUESTION_STATUS_META: Record<QuestionStatus, { label: string; color: ChipProps['color'] }> = {
  pending: { label: '待入库', color: 'warning' },
  published: { label: '已入库', color: 'success' },
};

const IMPORT_STATUS_META: Record<ImportStatus, { label: string; color: ChipProps['color'] }> = {
  parsing: { label: '解析中', color: 'info' },
  pending_confirm: { label: '待确认', color: 'warning' },
  committed: { label: '已入库', color: 'success' },
  failed: { label: '失败', color: 'error' },
};

const FILE_STATUS_META: Record<string, { label: string; color: ChipProps['color'] }> = {
  parsed: { label: '已解析', color: 'success' },
  failed: { label: '失败', color: 'error' },
};

function resolve(kind: ChipKind, value: string): { label: string; color: ChipProps['color'] } {
  switch (kind) {
    case 'qtype':
      return QTYPE_META[value as QType] ?? { label: value, color: 'default' };
    case 'anomaly':
      return ANOMALY_META[value as AnomalyType] ?? { label: value, color: 'default' };
    case 'question_status':
      return QUESTION_STATUS_META[value as QuestionStatus] ?? { label: value, color: 'default' };
    case 'import_status':
      return IMPORT_STATUS_META[value as ImportStatus] ?? { label: value, color: 'default' };
    case 'file_status':
      return FILE_STATUS_META[value] ?? { label: value, color: 'default' };
    default:
      return { label: value, color: 'default' };
  }
}

/** 徽标组件。 */
export default function StatusChip({ kind, value, size = 'small', muted = false }: StatusChipProps): JSX.Element {
  const meta = resolve(kind, value);
  return (
    <Chip
      size={size}
      label={meta.label}
      color={muted ? 'default' : meta.color}
      variant={muted ? 'outlined' : 'filled'}
      sx={{ fontSize: 12, height: 22 }}
    />
  );
}
