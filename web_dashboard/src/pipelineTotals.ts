export type ExtractionWindow = {
  days: number;
  sources: Array<{ source: string; attempted: number; succeeded: number }>;
};

export type PipelineTotals = {
  /** 0-1, or null before anything was attempted. */
  successRate: number | null;
  saved: number;
  sources: number;
};

/** Article extraction summed across every news source in the window. */
export function pipelineTotals(extraction: ExtractionWindow): PipelineTotals {
  const attempted = extraction.sources.reduce((sum, source) => sum + source.attempted, 0);
  const saved = extraction.sources.reduce((sum, source) => sum + source.succeeded, 0);
  return { successRate: attempted > 0 ? saved / attempted : null, saved, sources: extraction.sources.length };
}
