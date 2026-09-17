export type PresentationFormat = 'paragraph' | 'list' | 'table';

export interface PresentationDecision {
  format: PresentationFormat;
  content: string | null;
  reason: string | null;
}

export interface QueryResult {
  rows: Record<string, unknown>[];
  presentation: PresentationDecision | null;
}