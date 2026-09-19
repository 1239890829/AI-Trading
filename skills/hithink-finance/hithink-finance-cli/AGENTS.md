# hithink-finance-cli Agent Instructions

Read the monorepo root `AGENTS.md`, this directory's `README.md`, and the current
`package.json` scripts before changing this subproject.

- Treat `hithink-finance-cli/` as the Node.js project root.
- Keep the runtime, commands, database, and implementation independent from `../python/`.
- Implement behavior test-first and keep machine-readable stdout separate from diagnostics on stderr.
- Never write API keys or other credentials to configuration, fixtures, logs, or commits.
- For dependency security fixes, keep `package.json` and `npm-shrinkwrap.json` aligned, prefer the smallest compatible patch set, reinstall with `npm ci --ignore-scripts`, run `npm run verify`, and audit against the official npm registry (`npm audit --registry=https://registry.npmjs.org`). Keep transitive security upgrades when the official audit proves they are required; if npm's local metavulnerability cache contradicts the installed/locked patched version, recheck with a fresh temporary npm cache. Do not use `npm audit fix --force`.
- Keep CLI documentation focused on commands and runtime semantics; link `../docs/api/` instead of copying upstream response-field contracts.
- When command names, options, output/error semantics, or capability routing change, edit `scripts/generate-contracts.mjs`, run `npm run generate:contracts`, and commit the regenerated `skills/`, `schemas/`, and `skills/manifest.json` updates with the code change.
