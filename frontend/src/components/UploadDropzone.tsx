/** 多文件拖拽 / 选择上传区。 */
import { useRef, useState } from 'react';
import type { ChangeEvent, DragEvent } from 'react';
import { Alert, Box, Button, Stack, Typography } from '@mui/material';
import CloudUploadIcon from '@mui/icons-material/CloudUpload';

const ACCEPT_EXT = ['.docx', '.txt', '.pdf', '.json'];
const MAX_FILE_SIZE = 20 * 1024 * 1024;
const MAX_FILES = 20;

export interface UploadDropzoneProps {
  onFiles: (files: File[]) => void;
  disabled?: boolean;
  /** 单次最多文件数（默认 20，与后端一致）。 */
  maxFiles?: number;
}

function isAccepted(file: File): boolean {
  const lower = file.name.toLowerCase();
  return ACCEPT_EXT.some((ext) => lower.endsWith(ext));
}

/** 拖拽上传组件。 */
export default function UploadDropzone({
  onFiles,
  disabled = false,
  maxFiles = MAX_FILES,
}: UploadDropzoneProps): JSX.Element {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState('');

  const deliver = (list: FileList | null): void => {
    setError('');
    if (!list || list.length === 0) {
      return;
    }
    const files = Array.from(list);
    const rejectedType = files.filter((file) => !isAccepted(file));
    const rejectedSize = files.filter((file) => file.size > MAX_FILE_SIZE);
    const accepted = files.filter((file) => isAccepted(file) && file.size <= MAX_FILE_SIZE);

    if (rejectedType.length > 0) {
      setError(`已忽略 ${rejectedType.length} 个不支持的文件（仅接受 DOCX / TXT / PDF / JSON）`);
    } else if (rejectedSize.length > 0) {
      setError(`已忽略 ${rejectedSize.length} 个超过 20MB 的文件`);
    }
    if (accepted.length > maxFiles) {
      setError(`单次最多上传 ${maxFiles} 个文件，已截断`);
    }
    const finalFiles = accepted.slice(0, maxFiles);
    if (finalFiles.length > 0) {
      onFiles(finalFiles);
    }
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>): void => {
    event.preventDefault();
    setDragging(false);
    if (disabled) {
      return;
    }
    deliver(event.dataTransfer.files);
  };

  const handleChange = (event: ChangeEvent<HTMLInputElement>): void => {
    deliver(event.target.files);
    event.target.value = '';
  };

  return (
    <Box>
      <Box
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) {
            setDragging(true);
          }
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        onClick={() => {
          if (!disabled) {
            inputRef.current?.click();
          }
        }}
        sx={{
          border: '2px dashed',
          borderColor: dragging ? 'primary.main' : 'grey.400',
          bgcolor: dragging ? 'primary.50' : '#fff',
          borderRadius: 2,
          p: 5,
          textAlign: 'center',
          cursor: disabled ? 'not-allowed' : 'pointer',
          transition: 'all .15s',
          opacity: disabled ? 0.6 : 1,
        }}
      >
        <Stack spacing={1} alignItems="center">
          <CloudUploadIcon color="primary" sx={{ fontSize: 42 }} />
          <Typography variant="subtitle1">拖拽 DOCX / TXT / PDF / JSON 到此，或点击选择文件</Typography>
          <Typography variant="caption" color="text.secondary">
            支持多文件批量上传（单次最多 {maxFiles} 个，单个不超过 20MB）
          </Typography>
          <Button variant="contained" size="small" disabled={disabled} sx={{ mt: 1 }}>
            选择文件
          </Button>
        </Stack>
      </Box>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT_EXT.join(',')}
        hidden
        onChange={handleChange}
      />
      {error ? (
        <Alert severity="warning" sx={{ mt: 1 }} onClose={() => setError('')}>
          {error}
        </Alert>
      ) : null}
    </Box>
  );
}
