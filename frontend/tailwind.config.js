/** Tailwind 配置：扫描 src 下的 ts/tsx；关闭 preflight 以免与 MUI 基础样式冲突。 */
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  corePlugins: {
    preflight: false,
  },
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#eef4ff',
          100: '#d9e6ff',
          500: '#1f6feb',
          700: '#1657bd',
        },
      },
    },
  },
  plugins: [],
};
