import tseslint from "typescript-eslint";

export default [
  {
    ignores: ["dist/**"],
  },
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.ts"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": "off",
    },
  },
];
