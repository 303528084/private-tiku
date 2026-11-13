/** 路由表与整体布局。 */
import { Box, Button, Typography } from '@mui/material';
import { Link as RouterLink, Outlet, Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import Login from './pages/Login';
import SearchPage from './pages/SearchPage';
import ImportPage from './pages/ImportPage';
import AnomalyPage from './pages/AnomalyPage';
import QuestionDetailPage from './pages/QuestionDetailPage';
import QuestionCreatePage from './pages/QuestionCreatePage';
import StatsPage from './pages/StatsPage';

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

/** 应用根组件。 */
export default function App(): JSX.Element {
  return (
    <Routes>
      {/* 登录页独立布局：不套侧栏，避免未登录时 Layout 去拉 /api/stats 造成 401 噪音 */}
      <Route path="/login" element={<Login />} />
      <Route element={<LayoutRoute />}>
        <Route path="/" element={<SearchPage />} />
        <Route path="/import" element={<ImportPage />} />
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
