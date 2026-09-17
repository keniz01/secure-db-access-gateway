import { useState } from 'react';
import type { QueryResult } from '../../models/query-result';
import { ListRenderer } from './ListRenderer';
import { ParagraphRenderer } from './ParagraphRenderer';
import { ResultsTable } from './ResultsTable';

interface ResultRendererProps {
  result: QueryResult;
}

const FORMAT_LABELS: Record<string, string> = {
  paragraph: 'Summary',
  list: 'List summary',
};

export const ResultRenderer = ({ result }: ResultRendererProps): React.JSX.Element => {
  const [showingTable, setShowingTable] = useState(false);
  const decision = result.presentation;
  const hasGeneratedText =
    decision !== null && decision.format !== 'table' && Boolean(decision.content);
  const [showGeneratedText, setShowGeneratedText] = useState(hasGeneratedText);

  if (hasGeneratedText && showGeneratedText && !showingTable) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <span className="inline-flex items-center px-3 py-1 text-xs font-medium text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-full">
            AI-generated {FORMAT_LABELS[decision.format] ?? 'text'}
            {decision.reason ? ` — ${decision.reason}` : ''}
          </span>
          <button
            type="button"
            onClick={() => setShowingTable(true)}
            className="px-3 py-1.5 text-sm font-medium text-indigo-600 bg-white border border-indigo-300 rounded-md hover:bg-indigo-50"
          >
            View table
          </button>
        </div>
        {decision.format === 'paragraph' ? (
          <ParagraphRenderer content={decision.content as string} />
        ) : (
          <ListRenderer content={decision.content as string} />
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {hasGeneratedText && (
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={() => {
              setShowingTable(false);
              setShowGeneratedText(true);
            }}
            className="px-3 py-1.5 text-sm font-medium text-indigo-600 bg-white border border-indigo-300 rounded-md hover:bg-indigo-50"
          >
            View summary
          </button>
        </div>
      )}
      <ResultsTable data={result.rows} />
    </div>
  );
};