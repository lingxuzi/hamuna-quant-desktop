// Flat config —— 本项目此前从无 ESLint 配置 (`pnpm lint` 一直报 eslint not found,
// CI 也不跑 lint)。上游 300+ 文件零 lint 历史, 一次性全量合规是上千行无关改动。
// 故: 只把「真会出错」的规则留 error, 上游遗留的风格/严格类降为 warn 或关闭,
// 让 `pnpm lint` 有信号且不淹没真问题。新增代码请勿新增 error。
import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default [
  { ignores: ['dist', 'node_modules', 'coverage', '*.config.js'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,

      // ---- 保留 error: 真会出错 ----
      'react-hooks/rules-of-hooks': 'error',

      // ---- js.configs.recommended 的代码卫生规则: 上游 23 处, 降级 ----
      'no-useless-escape': 'warn',
      'no-empty': 'warn',
      'no-useless-assignment': 'warn',
      'prefer-const': 'warn',
      '@typescript-eslint/no-unused-expressions': 'warn',
      // 上游 3 处 (Dashboard 全角空格 / paper 执行客户端 throw 无 cause): 遗留
      'no-irregular-whitespace': 'warn',
      'preserve-caught-error': 'warn',

      // ---- 上游遗留, 降级 ----
      // 464 处 any: 上游大量 API 边界用 any, 逐个补类型是独立工程
      '@typescript-eslint/no-explicit-any': 'warn',
      // 12 处既有死变量: 属「改动前已存在的死代码」, 不擅自删
      '@typescript-eslint/no-unused-vars': [
        'warn',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' },
      ],
      'no-unused-expressions': 'warn',
      // React Compiler 时代的严格规则, 对既有 300+ 文件是噪音
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/refs': 'warn',
      'react-hooks/purity': 'warn',
      'react-hooks/immutability': 'warn',
      'react-hooks/preserve-manual-memoization': 'warn',
      'react-hooks/incompatible-library': 'warn',
      'react-hooks/globals': 'warn',
      // 下划线开头 = 有意忽略的依赖, 既有写法
      'react-hooks/exhaustive-deps': 'warn',
      // 单文件多导出的既有组织方式
      'react-refresh/only-export-components': 'off',
    },
  },
  {
    // `_DtStockTable` 是既有约定的「内部组件」命名(下划线前缀), 规则误判为非组件。
    // 若要恢复此文件的检查, 把该组件改名为 DtStockTable 并同步 1484 行调用点。
    files: ['src/custom/review/Page.tsx'],
    rules: { 'react-hooks/rules-of-hooks': 'off' },
  },
]
