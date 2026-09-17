export type PresentationFormat = 'paragraph' | 'list' | 'chart' | 'table';

export interface PresentationDecision {
  format: PresentationFormat;
  content: string | null;
  reason: string | null;
  columnLabels: Record<string, string> | null;
}

export interface QueryResult {
  rows: Record<string, unknown>[];
  presentation: PresentationDecision | null;
}
