/** 整体布局：响应式侧边导航 + 顶部栏（面包屑/标语）+ 限宽内容区。 */
import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Link as RouterLink, useLocation } from 'react-router-dom';
import {
  AppBar,
  Box,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Toolbar,
  Typography,
  useMediaQuery,
  useTheme,
} from '@mui/material';
import MenuIcon from '@mui/icons-material/Menu';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import SearchIcon from '@mui/icons-material/Search';
import ReportProblemIcon from '@mui/icons-material/ReportProblem';
import BarChartIcon from '@mui/icons-material/BarChart';
import AddCircleOutlineIcon from '@mui/icons-material/AddCircleOutline';
import MenuBookIcon from '@mui/icons-material/MenuBook';
import { api, toErrorMessage } from '../api/client';
import type { StatsResult } from '../api/client';

const DRAWER_WIDTH = 232;
/** 题目/表格阅读行宽上限，避免宽屏一行过长。 */
const CONTENT_MAX_WIDTH = 1200;

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

/**
 * 顶栏面包屑的模块名：把路由路径映射为中文名。
 *
 * 注意 '/questions/new' 必须先于 '/questions/' 前缀判断，否则会误判为「题目详情」。
 */
function currentNavLabel(pathname: string): string {
  if (pathname === '/') {
    return '搜索题库';
  }
  if (pathname === '/questions/new') {
    return '手动录题';
  }
  if (pathname.startsWith('/questions/')) {
    return '题目详情';
  }
  if (pathname === '/import') {
    return '导入文档';
  }
  if (pathname === '/anomalies') {
    return '异常校对';
  }
  if (pathname === '/stats') {
    return '导入统计';
  }
  return '题库';
}

export interface LayoutProps {
  children: ReactNode;
}

/** 布局组件。 */
export default function Layout({ children }: LayoutProps): JSX.Element {
  const theme = useTheme();
  // md 断点（默认 900px）以下视为窄屏：侧栏改用临时抽屉 + 汉堡菜单。
  const isMobile = useMediaQuery(theme.breakpoints.down('md'));
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [stats, setStats] = useState<StatsResult | null>(null);
  const [statsError, setStatsError] = useState('');

  // 路由切换后自动收起临时抽屉（桌面 permanent 抽屉始终展开，不受影响）。
  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

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

  const handleDrawerToggle = (): void => {
    setMobileOpen((previous) => !previous);
  };

  const handleDrawerClose = (): void => {
    setMobileOpen(false);
  };

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
        sx={{
          width: isMobile ? '100%' : `calc(100% - ${DRAWER_WIDTH}px)`,
          ml: isMobile ? 0 : `${DRAWER_WIDTH}px`,
          borderBottom: '1px solid #e6e9f0',
        }}
      >
        <Toolbar variant="dense" sx={{ gap: 1 }}>
          {isMobile ? (
            <IconButton
              size="small"
              color="inherit"
              aria-label="打开导航"
              onClick={handleDrawerToggle}
              sx={{ mr: 0.5 }}
            >
              <MenuIcon />
            </IconButton>
          ) : null}
          <Typography variant="caption" color="text.secondary" noWrap>
            私有护理题库 / {currentNavLabel(location.pathname)}
          </Typography>
          <Box sx={{ flexGrow: 1 }} />
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: { xs: 'none', sm: 'block' } }}
          >
            JSON / Markdown 导入 · 手动录题 · 全文检索 · 异常校对
          </Typography>
        </Toolbar>
      </AppBar>
      <Drawer
        variant={isMobile ? 'temporary' : 'permanent'}
        open={isMobile ? mobileOpen : true}
        onClose={handleDrawerClose}
        ModalProps={{ keepMounted: true }}
        sx={{
          // permanent 抽屉是 flex 子项，需显式宽度占位；temporary 抽屉是 fixed Modal，
          // 根节点不能限宽，否则遮罩只覆盖 232px、右侧点击无法关闭。
          ...(isMobile ? {} : { width: DRAWER_WIDTH, flexShrink: 0 }),
          '& .MuiDrawer-paper': { width: DRAWER_WIDTH, boxSizing: 'border-box' },
        }}
      >
        {drawer}
      </Drawer>
      <Box component="main" sx={{ flexGrow: 1, minWidth: 0, bgcolor: '#f5f7fb' }}>
        <Toolbar variant="dense" />
        <Box sx={{ p: { xs: 2, sm: 3 }, width: '100%', maxWidth: CONTENT_MAX_WIDTH, mx: 'auto' }}>
          {children}
        </Box>
      </Box>
    </Box>
  );
}
