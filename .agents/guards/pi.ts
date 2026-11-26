// pi's adapter of the agents' guards (agents/README.md "The guards of a run"). pi loads it as an extension:
// .pi/settings.json names it, and pi reads that file in a trusted project (the agents' clones: `agent_user.py user`
// writes their trust). On each tool call, before the tool runs, it turns pi's call into an action of the guards' contract
// (.agents/guards/guard.py) and a refusal into pi's own: `{ block: true, reason }`, and the agent sees the reason.
// `edit` and `write` are an `edit` of their `path` (read from the session's folder, `~` and a leading `@` as pi reads
// them); `bash` is a `shell` command. Every other call passes, and so does a call that the guards cannot answer (no
// python3, a broken reply): as Claude Code's adapter, whose crash refuses nothing. No import of pi's own package: the file
// runs under pi (jiti) and under Node's test runner (a product's unit tests) alike.

import { spawnSync } from "node:child_process";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const GUARD = join(dirname(fileURLToPath(import.meta.url)), "guard.py");
const EDITS = new Set(["edit", "write"]);

type Action = { action: "edit"; path: string; cwd: string } | { action: "shell"; command: string; cwd: string };
type Answer = { allow: boolean; message?: string };
type ToolCall = { toolName?: unknown; input?: unknown };
type Pi = { on(event: "tool_call", handler: (event: ToolCall, ctx: { cwd: string }) => unknown): void };

/** The file that pi's tool would change: `~` is the home folder, a leading `@` goes, a relative path is read from `cwd`. */
export function filePath(raw: string, cwd: string): string {
  const path = raw.startsWith("@") ? raw.slice(1) : raw;
  if (path === "~") return homedir();
  if (path.startsWith("~/")) return join(homedir(), path.slice(2));
  return resolve(cwd, path);
}

/** The guards' action for one pi tool call, or null when no guard reads it. */
export function action(event: ToolCall, cwd: string): Action | null {
  const input = event.input && typeof event.input === "object" ? (event.input as Record<string, unknown>) : {};
  if (typeof event.toolName !== "string") return null;
  if (EDITS.has(event.toolName) && typeof input.path === "string" && input.path) {
    return { action: "edit", path: filePath(input.path, cwd), cwd };
  }
  if (event.toolName === "bash" && typeof input.command === "string") {
    return { action: "shell", command: input.command, cwd };
  }
  return null;
}

/** The guards' answer: guard.py's stdout, or an allow when it gives none that reads. */
export function decide(act: Action): Answer {
  const done = spawnSync("python3", ["-I", GUARD], { input: JSON.stringify(act), encoding: "utf8", timeout: 10000 });
  try {
    const answer = JSON.parse(done.stdout) as Answer;
    return answer.allow === false && typeof answer.message === "string" ? answer : { allow: true };
  } catch {
    return { allow: true };
  }
}

export default function guards(pi: Pi): void {
  pi.on("tool_call", (event, ctx) => {
    const act = action(event, ctx.cwd);
    const answer = act ? decide(act) : { allow: true };
    return answer.allow ? undefined : { block: true, reason: answer.message };
  });
}
