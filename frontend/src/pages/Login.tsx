/**
 * 门禁登录页（v1.4 T01）。
 *
 * 独立居中的口令卡片，**不套** `Layout`（未登录时侧栏会去拉 /api/stats，白拿 401）。
 * 登录成功后整页跳回 `/`；口令错误时展示后端 `message`（"口令错误"）。
 * 后端未启用门禁时（`/api/auth/status` 返回 enabled=false）自动放行到 `/`。
 */
import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  InputAdornment,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import LockOutlinedIcon from '@mui/icons-material/LockOutlined';
import MenuBookIcon from '@mui/icons-material/MenuBook';
import { useNavigate } from 'react-router-dom';
import { api, toErrorMessage } from '../api/client';

/** 登录页。 */
export default function Login(): JSX.Element {
  const navigate = useNavigate();
  const [token, setToken] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [checking, setChecking] = useState(true);

  // 启动时问一次门禁状态：未启用 → 直接放行（避免用户看到无意义的登录框）。
  useEffect(() => {
    let active = true;
    api
      .authStatus()
      .then((status) => {
        if (!active) {
          return;
        }
        if (!status.enabled || status.authenticated) {
          navigate('/', { replace: true });
          return;
        }
        setChecking(false);
      })
      .catch(() => {
        // 状态查询失败不影响手工登录（后端可能刚重启）
        if (active) {
          setChecking(false);
        }
      });
    return () => {
      active = false;
    };
  }, [navigate]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (submitting) {
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      await api.authLogin(token);
      navigate('/', { replace: true });
    } catch (caught) {
      setError(toErrorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Box
      sx={{
        minHeight: '100vh',
        bgcolor: '#f5f7fb',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        px: 2,
        py: 6,
      }}
    >
      <Card variant="outlined" sx={{ width: '100%', maxWidth: 420, borderRadius: 2 }}>
        <CardContent sx={{ p: { xs: 3, sm: 4 } }}>
          <Stack spacing={1} alignItems="center" sx={{ mb: 3 }}>
            <MenuBookIcon color="primary" sx={{ fontSize: 34 }} />
            <Typography variant="h6" sx={{ fontWeight: 700 }}>
              私有护理题库
            </Typography>
            <Typography variant="body2" color="text.secondary" align="center">
              该部署已启用访问口令，请输入后继续。
            </Typography>
          </Stack>

          {checking ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
              <CircularProgress size={24} />
            </Box>
          ) : (
            <Box component="form" onSubmit={(event) => void handleSubmit(event)} noValidate>
              <Stack spacing={2}>
                <TextField
                  label="访问口令"
                  type="password"
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                  autoFocus
                  fullWidth
                  autoComplete="current-password"
                  error={Boolean(error)}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">
                        <LockOutlinedIcon fontSize="small" color="action" />
                      </InputAdornment>
                    ),
                  }}
                />
                {error ? <Alert severity="error">{error}</Alert> : null}
                <Button
                  type="submit"
                  variant="contained"
                  size="large"
                  disabled={submitting || token.length === 0}
                  startIcon={submitting ? <CircularProgress size={16} color="inherit" /> : null}
                >
                  {submitting ? '正在验证…' : '进入题库'}
                </Button>
                <Typography variant="caption" color="text.secondary" align="center">
                  口令由部署者通过环境变量 TIKU_ACCESS_TOKEN 设置，登录状态保持 7 天。
                </Typography>
              </Stack>
            </Box>
          )}
        </CardContent>
      </Card>
    </Box>
  );
}
