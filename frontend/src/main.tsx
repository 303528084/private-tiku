/** React 挂载入口：ThemeProvider + CssBaseline + BrowserRouter。 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { CssBaseline, ThemeProvider, createTheme } from '@mui/material';
import App from './App';
import './index.css';

const theme = createTheme({
  palette: {
    mode: 'light',
    primary: { main: '#1f6feb' },
    secondary: { main: '#7b1fa2' },
    background: { default: '#f5f7fb', paper: '#ffffff' },
  },
  shape: { borderRadius: 8 },
  typography: {
    fontFamily:
      "'Segoe UI', 'PingFang SC', 'Microsoft YaHei', system-ui, -apple-system, sans-serif",
  },
});

const container = document.getElementById('root');
if (!container) {
  throw new Error('#root 容器不存在，index.html 可能被破坏');
}

/**
 * 注册 Service Worker（v1.4 T05 PWA 壳）。
 *
 * **只在生产构建注册**：dev server 下 SW 会拦截 Vite 的 HMR 请求、缓存中间产物，
 * 导致改代码后页面不更新，排查成本极高。
 *
 * 注册失败必须静默：SW 只提供「可安装 + 离线壳」的增强，拿不到也不该影响应用启动
 * （例如浏览器禁用了第三方 Cookie/存储、或部署在非安全上下文 http 上）。
 */
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {
      // 静默降级：无 SW 时应用照常可用
    });
  });
}

ReactDOM.createRoot(container).render(
  <React.StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ThemeProvider>
  </React.StrictMode>,
);
