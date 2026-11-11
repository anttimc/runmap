/** Structured local bridge to the separately installed Suunto route importer. */
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const [installation, operation, directory] = process.argv.slice(2);
const load = (name) => import(pathToFileURL(resolve(installation, "src", name)).href);
let code = "installation";
try {
  const { readCredentials } = await load("config.ts");
  const { loginClient, accountUsername, AuthCheckError } = await load("suunto/auth.ts");
  const { createRoute, selectCreatedRoute, ImportError } = await load("suunto/import.ts");
  const receipt = resolve(directory, "receipt.json");
  try {
    let payload;
    if (operation === "import") {
      code = "validation";
      const { parseGpx } = await load("gpx.ts");
      const { buildRoute } = await load("suunto/route-builder.ts");
      const input = JSON.parse(await readFile(resolve(directory, "operation.json"), "utf8"));
      const route = parseGpx(await readFile(resolve(directory, "route.gpx"), "utf8"));
      buildRoute(route, "preview-owner", input.name);
      code = "authentication";
      const client = await loginClient(readCredentials());
      payload = buildRoute(route, await accountUsername(client), input.name);
      code = "creation";
      await createRoute(client, payload, receipt);
      if (input.use_in_watch) {
        code = "selection";
        await selectCreatedRoute(client, receipt);
      }
    } else if (operation === "select") {
      code = "authentication";
      const client = await loginClient(readCredentials());
      code = "selection";
      await selectCreatedRoute(client, receipt);
    } else {
      throw new Error("Unsupported operation");
    }
    process.stdout.write(JSON.stringify({ error: null }));
  } catch (error) {
    if (error instanceof AuthCheckError) code = "authentication";
    if (error instanceof ImportError) code = error.phase;
    throw error;
  }
} catch {
  // Upstream errors can contain credentials or private account responses.
  process.stdout.write(JSON.stringify({ error: code }));
  process.exitCode = 1;
}
