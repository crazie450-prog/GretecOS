// Run one of the core's lookup tools (POST /api/tools/{name}) and keep its card.
import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { Card } from "../cards/types.gen";

export interface ToolResult {
  card: Card | null;
  loading: boolean;
  error: string | null;
}

export function useTool(name: string | null, args: Record<string, unknown>, refreshKey = 0): ToolResult {
  const key = JSON.stringify(args);
  const [result, setResult] = useState<ToolResult>({ card: null, loading: name !== null, error: null });
  useEffect(() => {
    if (name === null) {
      setResult({ card: null, loading: false, error: null });
      return;
    }
    let live = true;
    setResult((r) => ({ ...r, loading: true }));
    api
      .tool(name, JSON.parse(key) as Record<string, unknown>)
      .then((r) => live && setResult({ card: r.card, loading: false, error: null }))
      .catch((e: unknown) => live && setResult({ card: null, loading: false, error: e instanceof ApiError ? e.message : String(e) }));
    return () => {
      live = false;
    };
  }, [name, key, refreshKey]);
  return result;
}
