/** 路由表与整体布局。 */
import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Box, Button, CircularProgress, Typography } from '@mui/material';
import {
  Link as RouterLink,
  Navigate,
  Outlet,
  Route,
  Routes,
  useNavigate,
} from 'react-router-dom';
import Layout from './components/Layout';
import Login from './pages/Login';
import SearchPage from './pages/SearchPage';
import ImportPage from './pages/ImportPage';
import AnomalyPage from './pages/AnomalyPage';
import QuestionDetailPage from './pages/QuestionDetailPage';
import QuestionCreatePage from './pages/QuestionCreatePage';
import StatsPage from './pages/StatsPage';
import { api } from './api/client';

/** 404 兜底页。 */
function NotFoundPage(): JSX.Element {
  return (
    <Box sx={{ textAlign: 'center', py: 10 }}>
      <Typography variant="h4" sx={{ fontWeight: 700, mb: 1 }}>
        404
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        页面不存在，可能链接已失效。
      </Typography>
      <Button variant="contained" component={RouterLink} to="/">
        返回搜索题库
      </Button>
    </Box>
  );
}

/** 带侧栏布局的路由外壳（v1.4 T01 引入，供登录页与主应用共享同一份路由表）。 */
function LayoutRoute(): JSX.Element {
  return (
    <Layout>
      <Outlet />
    </Layout>
  );
}

/**
 * 门禁前置守卫（v1.4 T05）。
 *
 * 为什么需要：v1.4 T01 的兜底是「某个页面首次请求 401 → `client.ts` 整页跳转
 * `/login`」。用户会先看到一下主应用骨架（侧栏、统计数字的占位）再被踢走，观感
 * 很差。这里在挂载时先问一次 `/api/auth/status`，未通过就**在渲染 Layout 之前**
 * 跳走，骨架一帧都不会出现。
 *
 * 刻意保持极简：
 * - 不引入全局状态库（一次请求、一个布尔值，用 `useState` 足够）；
 * - 不改 `client.ts` 的 401 拦截逻辑（那套已验证不会造成 401 风暴）；
 * - 状态接口失败时**放行**而不是卡死：后端刚重启/网络抖动时，宁可让页面自己
 *   显示错误，也不能让用户对着转圈干等；
 * - `/login` 路由在守卫之外，登录页不受影响。
 */
function AuthGuard({ children }: { children: ReactNode }): JSX.Element {
  const navigate = useNavigate();
  /** true 表示还在等 `/api/auth/status`，此时不渲染任何应用骨架。 */
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let active = true;
    api
      .authStatus()
      .then((status) => {
        if (!active) {
          return;
        }
        if (status.enabled && !status.authenticated) {
          navigate('/login', { replace: true });
          return;
        }
        setChecking(false);
      })
      .catch(() => {
        // 查询失败不阻断：交给各页面自己的错误提示（与 v1.3 行为一致）
        if (active) {
          setChecking(false);
        }
      });
    return () => {
      active = false;
    };
  }, [navigate]);

  if (checking) {
    return (
      <Box
        sx={{
          minHeight: '100vh',
          bgcolor: '#f5f7fb',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <CircularProgress size={28} />
      </Box>
    );
  }
  return <>{children}</>;
}

/** 应用根组件。 */
export default function App(): JSX.Element {
  return (
    <Routes>
      {/* 登录页独立布局：不套侧栏，避免未登录时 Layout 去拉 /api/stats 造成 401 噪音 */}
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <AuthGuard>
            <LayoutRoute />
          </AuthGuard>
        }
      >
        <Route path="/" element={<SearchPage />} />
        <Route path="/import" element={<ImportPage />} />
        {/* 兼容旧链接/手误：/imports 不是真实路由，重定向到 /import（v1.4 T05） */}
        <Route path="/imports" element={<Navigate to="/import" replace />} />
        <Route path="/anomalies" element={<AnomalyPage />} />
        <Route path="/stats" element={<StatsPage />} />
        {/* 必须排在 /questions/:id 之前，否则 "new" 会被当成 id */}
        <Route path="/questions/new" element={<QuestionCreatePage />} />
        <Route path="/questions/:id" element={<QuestionDetailPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
