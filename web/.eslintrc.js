module.exports = {
  root: true,
  env: {
    browser: true,
    es2021: true,
    node: true,
  },
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
  },
  plugins: [
    '@typescript-eslint',
    'security',
  ],
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:security/recommended',
  ],
  rules: {
    // Type strictness — codebase uses `any` extensively (tsconfig strict:false)
    '@typescript-eslint/no-explicit-any': 'off',
    // Security rules from eslint-plugin-security
    'security/detect-object-injection': 'off',
    'security/detect-non-literal-fs-filename': 'warn',
    'security/detect-unsafe-regex': 'error',
    'security/detect-new-buffer': 'error',
    'security/detect-buffer-noassert': 'error',
    'security/detect-child-process': 'warn',
    'security/detect-pseudoRandomBytes': 'warn',
  },
  ignorePatterns: [
    'node_modules/',
    'dist/',
    'assets/lib/',
  ],
};
