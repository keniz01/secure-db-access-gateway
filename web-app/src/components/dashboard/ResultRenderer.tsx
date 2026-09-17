import { useState } from 'react';
import type { QueryResult } from '../../models/query-result';
import { ChartRenderer } from './ChartRenderer';
import { ListRenderer } from './ListRenderer';
import { ParagraphRenderer } from './ParagraphRenderer';
import { ResultsTable } from './ResultsTable';
import { SummaryCard } from './SummaryCard';

interface ResultRendererProps {
  result: QueryResult;
}

const FORMAT_LABELS: Record<string, string> = {
  paragraph: 'Summary',
  list: 'List summary',
  chart: 'Chart',
  table: 'Summary',
};

// Small/scalar results are fully answered by the AI summary sentence; echoing
// the same data back in a table below it is redundant. Show one or the other,
// with the table still one click away.
const MAX_SUMMARY_ONLY_ROWS = 2;

export const ResultRenderer = ({ result }: ResultRendererProps): React.JSX.Element => {
  const [tableView, setTableView] = useState(false);

  const decision = result.presentation;
  const format = decision?.format ?? 'table';
  const content = decision?.content ?? null;

  const isText = format === 'paragraph' || format === 'list';
  const isChart = format === 'chart';
  const summaryOnlyTable =
    format === 'table' && Boolean(content) && result.rows.length <= MAX_SUMMARY_ONLY_ROWS;
  const isSummaryOnly = isText || isChart || summaryOnlyTable;
  const label = FORMAT_LABELS[format] ?? 'Presentation';
  const showPresentation = !tableView && isSummaryOnly;
  const hasToggle = isSummaryOnly;

  const summaryBanner = content ? <SummaryCard content={content} /> : null;

  return (
    <div className="space-y-4">
      {decision && hasToggle && (
        <div className="flex items-center justify-between gap-4">
          <span className="inline-flex items-center px-3 py-1 text-xs font-medium text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-full">
            AI-generated {label}
            {decision.reason ? ` — ${decision.reason}` : ''}
          </span>
          <button
            type="button"
            onClick={() => setTableView((value) => !value)}
            className="px-3 py-1.5 text-sm font-medium text-indigo-600 bg-white border border-indigo-300 rounded-md hover:bg-indigo-50 shrink-0"
          >
            {tableView ? `View ${label.toLowerCase()}` : 'View table'}
          </button>
        </div>
      )}

      {showPresentation && isText && content && (
        format === 'paragraph' ? (
          <ParagraphRenderer content={content} />
        ) : (
          <ListRenderer content={content} />
        )
      )}

      {showPresentation && isChart && (
        <div className="space-y-4">
          {summaryBanner}
          <ChartRenderer data={result.rows} columnLabels={decision?.columnLabels} />
        </div>
      )}

      {showPresentation && summaryOnlyTable && summaryBanner}

      {!showPresentation && (
        <div className="space-y-4">
          {summaryBanner}
          <ResultsTable data={result.rows} />
        </div>
      )}
    </div>
  );
};
