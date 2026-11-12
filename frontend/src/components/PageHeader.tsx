/** 统一页面头部：标题 + 说明 + 可选操作区（纯展示组件，无副作用）。 */
import type { ReactNode } from 'react';
import { Box, Stack, Typography } from '@mui/material';

export interface PageHeaderProps {
  /** 页面主标题。 */
  title: string;
  /** 标题下方的补充说明，可为任意 ReactNode（如含 <b> 的富文本）。 */
  description?: ReactNode;
  /** 右侧操作区（按钮等），窄屏自动换到下一行。 */
  actions?: ReactNode;
}

/**
 * 统一各页面的头部排版，避免 h5 / h6 与底部间距各写各的。
 *
 * 布局：外层统一底部间距；内部 Stack 在 xs 竖排、sm 及以上横排；
 * 左侧放标题与说明，右侧放可选操作区。操作区用可换行容器包裹，
 * 保证窄屏下多个按钮不会撑破页面宽度。
 */
export default function PageHeader({ title, description, actions }: PageHeaderProps): JSX.Element {
  return (
    <Box sx={{ mb: 2.5 }}>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={1.5}
        justifyContent="space-between"
        alignItems={{ sm: 'flex-start' }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="h5" sx={{ fontWeight: 700, mb: 0.5 }}>
            {title}
          </Typography>
          {description ? (
            <Typography variant="body2" color="text.secondary">
              {description}
            </Typography>
          ) : null}
        </Box>
        {actions ? (
          <Box sx={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', flexShrink: 0 }}>
            {actions}
          </Box>
        ) : null}
      </Stack>
    </Box>
  );
}
