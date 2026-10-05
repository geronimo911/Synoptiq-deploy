export function reportError(error: unknown, context: Record<string, unknown> = {}) {
  console.error("Synoptiq runtime error", error, context);
}
