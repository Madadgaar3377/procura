import { spawn } from "node:child_process";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const nextCli = require.resolve("next/dist/bin/next");
const input = process.argv.slice(2);
const args = [];
for (let i = 0; i < input.length; i += 1) {
  if (input[i] === "--strictPort") continue;
  if (input[i] === "--host") {
    args.push("--hostname", input[i + 1] || "0.0.0.0");
    i += 1;
    continue;
  }
  args.push(input[i]);
}
const child = spawn(process.execPath, [nextCli, "dev", "--webpack", ...args], { stdio: "inherit" });
child.on("exit", (code) => process.exit(code ?? 0));
