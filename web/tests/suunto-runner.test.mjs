/** Verify the bridge using synthetic modules without Suunto network access. */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const runner = fileURLToPath(new URL("../../integrations/suunto/runner.mjs", import.meta.url));

function fixture(context) {
  const root = mkdtempSync(join(tmpdir(), "runmap-suunto-"));
  context.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(join(root, "src/suunto"), { recursive: true });
  writeFileSync(join(root, "package.json"), '{"type":"module"}');
  writeFileSync(join(root, "src/config.ts"), "export function readCredentials() { return {}; }");
  writeFileSync(
    join(root, "src/gpx.ts"),
    `
    export function parseGpx(xml) {
      if (xml !== 'valid') throw new Error('private input');
      return { points: [] };
    }
  `,
  );
  writeFileSync(
    join(root, "src/suunto/auth.ts"),
    `
    export class AuthCheckError extends Error {}
    export async function loginClient() { return {}; }
    export async function accountUsername() { return 'synthetic-owner'; }
  `,
  );
  writeFileSync(
    join(root, "src/suunto/route-builder.ts"),
    `
    export function buildRoute(route, owner, name) { return {owner, name}; }
  `,
  );
  writeFileSync(
    join(root, "src/suunto/import.ts"),
    `
    import { writeFile, readFile } from 'node:fs/promises';
    export class ImportError extends Error {}
    export async function createRoute(client, payload, receipt) {
      await writeFile(receipt, JSON.stringify({state:'created', ...payload}));
    }
    export async function selectCreatedRoute(client, receipt) {
      const previous = JSON.parse(await readFile(receipt, 'utf8'));
      if (previous.name === 'selection failure') throw new Error('private token');
      await writeFile(receipt, JSON.stringify({...previous, state:'selected'}));
    }
  `,
  );
  const operation = join(root, "operation");
  mkdirSync(operation);
  writeFileSync(join(operation, "route.gpx"), "valid");
  writeFileSync(
    join(operation, "operation.json"),
    JSON.stringify({ name: "Route", use_in_watch: true }),
  );
  return { root, operation };
}

test("bridge creates then selects and supports selection-only recovery", (context) => {
  const { root, operation } = fixture(context);
  const invoke = (action) =>
    JSON.parse(
      execFileSync(process.execPath, [runner, root, action, operation], { encoding: "utf8" }),
    );
  assert.deepEqual(invoke("import"), { error: null });
  const receipt = JSON.parse(readFileSync(join(operation, "receipt.json"), "utf8"));
  assert.equal(receipt.state, "selected");
  assert.equal(receipt.owner, "synthetic-owner");
  assert.deepEqual(invoke("select"), { error: null });
});

test("bridge sanitizes selection failure and retains creation receipt", (context) => {
  const { root, operation } = fixture(context);
  writeFileSync(
    join(operation, "operation.json"),
    JSON.stringify({ name: "selection failure", use_in_watch: true }),
  );
  assert.throws(
    () =>
      execFileSync(process.execPath, [runner, root, "import", operation], {
        encoding: "utf8",
        stdio: "pipe",
      }),
    (error) => {
      assert.deepEqual(JSON.parse(error.stdout), { error: "selection" });
      assert.equal(error.stderr, "");
      return true;
    },
  );
  assert.equal(JSON.parse(readFileSync(join(operation, "receipt.json"), "utf8")).state, "created");
});

test("bridge rejects invalid GPX before authentication or writes", (context) => {
  const { root, operation } = fixture(context);
  writeFileSync(join(operation, "route.gpx"), "invalid");
  writeFileSync(
    join(root, "src/suunto/auth.ts"),
    `
    export class AuthCheckError extends Error {}
    export async function loginClient() { throw new AuthCheckError('must not log in'); }
    export async function accountUsername() { throw new Error(); }
  `,
  );
  assert.throws(
    () =>
      execFileSync(process.execPath, [runner, root, "import", operation], {
        encoding: "utf8",
        stdio: "pipe",
      }),
    (error) => {
      assert.deepEqual(JSON.parse(error.stdout), { error: "validation" });
      assert.equal(error.stderr, "");
      return true;
    },
  );
  assert.throws(() => readFileSync(join(operation, "receipt.json")));
});
