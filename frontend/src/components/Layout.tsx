/** 整体布局：侧边导航 + 顶部栏 + 内容区（含题库概览）。 */
import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Link as RouterLink, useLocation } from 'react-router-dom';
import {
  AppBar,
  Box,
  Divider,
  Drawer,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Toolbar,
  Typography,
} from '@mui/material';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import SearchIcon from '@mui/icons-material/Search';
import ReportProblemIcon from '@mui/icons-material/ReportProblem';
import BarChartIcon from '@mui/icons-material/BarChart';
import AddCircleOutlineIcon from '@mui/icons-material/AddCircleOutline';
import MenuBookIcon from '@mui/icons-material/MenuBook';
import { api, toErrorMessage } from '../api/client';
import type { StatsResult } from '../api/client';

const DRAWER_WIDTH = 232;

interface NavItem {
  to: string;
  label: string;
  icon: JSX.Element;
}

const NAV_ITEMS: NavItem[] = [
  { to: '/', label: '搜索题库', icon: <SearchIcon fontSize="small" /> },
  { to: '/questions/new', label: '手动录题', icon: <AddCircleOutlineIcon fontSize="small" /> },
  { to: '/import', label: '导入文档', icon: <UploadFileIcon fontSize="small" /> },
  { to: '/anomalies', label: '异常校对', icon: <ReportProblemIcon fontSize="small" /> },
  { to: '/stats', label: '导入统计', icon: <BarChartIcon fontSize="small" /> },
];

export interface LayoutProps {
  children: ReactNode;
}

/** 布局组件。 */
export default function Layout({ children }: LayoutProps): JSX.Element {
  const location = useLocation();
  const [stats, setStats] = useState<StatsResult | null>(null);
  const [statsError, setStatsError] = useState('');

  useEffect(() => {
    let active = true;
    api
      .stats()
      .then((data) => {
        if (active) {
          setStats(data);
          setStatsError('');
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setStatsError(toErrorMessage(error));
        }
      });
    return () => {
      active = false;
    };
  }, [location.pathname]);

  const drawer = (
    <Box sx={{ width: DRAWER_WIDTH, height: '100%', display: 'flex', flexDirection: 'column' }}>
      <Toolbar sx={{ gap: 1 }}>
        <MenuBookIcon color="primary" />
        <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
          私有护理题库
        </Typography>
      </Toolbar>
      <Divider />
      <List sx={{ px: 1, py: 1 }}>
        {NAV_ITEMS.map((item) => {
          const selected = item.to === '/' ? location.pathname === '/' : location.pathname.startsWith(item.to);
          return (
            <ListItemButton
              key={item.to}
              component={RouterLink}
              to={item.to}
              selected={selected}
              sx={{ borderRadius: 1.5, mb: 0.5 }}
            >
              <ListItemIcon sx={{ minWidth: 34 }}>{item.icon}</ListItemIcon>
              <ListItemText primary={item.label} primaryTypographyProps={{ fontSize: 14 }} />
            </ListItemButton>
          );
        })}
      </List>
      <Box sx={{ mt: 'auto', p: 2, bgcolor: 'grey.50', borderTop: '1px solid #eee' }}>
        <Typography variant="caption" color="text.secondary" display="block">
          题库量 {stats ? stats.total_published : '—'}
        </Typography>
        <Typography variant="caption" color="text.secondary" display="block">
          待入库 {stats ? stats.total_pending : '—'}
        </Typography>
        <Typography variant="caption" color="error.main" display="block">
          未处理异常 {stats ? stats.anomaly_open : '—'}
        </Typography>
        <Typography variant="caption" color="text.secondary" display="block">
          导入批次 {stats ? stats.total_imports : '—'}
        </Typography>
        {statsError ? (
          <Typography variant="caption" color="error.main" display="block" sx={{ mt: 1 }}>
            {statsError}
          </Typography>
        ) : null}
      </Box>
    </Box>
  );

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh' }}>
      <AppBar
        position="fixed"
        color="inherit"
        elevation={0}
        sx={{ width: `calc(100% - ${DRAWER_WIDTH}px)`, ml: `${DRAWER_WIDTH}px`, borderBottom: '1px solid #e6e9f0' }}
      >
        <Toolbar variant="dense">
          <Typography variant="subtitle2" color="text.secondary">
            JSON / Markdown 导入 · 手动录题 · 规则解析 · 全文检索 · 异常校对 · 一键导出
          </Typography>
        </Toolbar>
      </AppBar>
      <Drawer
        variant="permanent"
        sx={{
          width: DRAWER_WIDTH,
          flexShrink: 0,
          '& .MuiDrawer-paper': { width: DRAWER_WIDTH, boxSizing: 'border-box' },
        }}
      >
        {drawer}
      </Drawer>
      <Box component="main" sx={{ flexGrow: 1, minWidth: 0, bgcolor: '#f5f7fb' }}>
        <Toolbar variant="dense" />
        <Box sx={{ p: 3 }}>{children}</Box>
      </Box>
    </Box>
  );
}
