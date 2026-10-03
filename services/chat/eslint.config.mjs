import { fixupConfigRules } from '@eslint/compat';
import { defineConfig, globalIgnores } from 'eslint/config';
import nextVitals from 'eslint-config-next/core-web-vitals';
import nextTs from 'eslint-config-next/typescript';

const eslintConfig = defineConfig([
  // eslint-plugin-react (bundled by eslint-config-next) still calls context
  // methods ESLint 10 removed; fixupConfigRules shims them back.
  ...fixupConfigRules([...nextVitals, ...nextTs]),
  globalIgnores([
    '.next/**',
    'out/**',
    'build/**',
    'next-env.d.ts',
  ]),
]);

export default eslintConfig;
