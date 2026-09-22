import { useState } from 'react';
import { Table, BarChart3, FileText, Download } from 'lucide-react';
import type { QueryResult } from '../../models/query-result';
import { ResultRenderer } from '../dashboard/ResultRenderer';
import { ResultsTable } from '../dashboard/ResultsTable';
import { ChartRenderer } from '../dashboard/ChartRenderer';

type ResultTab = 'auto' | 'table' | 'chart' | 'summary';

interface ResultsPanelProps {
  result: QueryResult | null;
  error: string | null;
  isExecuting: boolean;
}

export const ResultsPanel = ({ result, error, isExecuting }: ResultsPanelProps) => {
  const [activeTab, setActiveTab] = useState<ResultTab>('auto');

  const hasResult = result !== null && !error;
  const rowCount = result?.rows.length ?? 0;
  const presentation = result?.presentation;
  const hasPresentation = Boolean(presentation?.content);

  const handleExportCsv = () => {
    if (!result?.rows.length) return;
    const columns = Object.keys(result.rows[0]);
    const header = columns.join(',');
    const rows = result.rows.map((row) =>
      columns.map((col) => {
        const val = row[col];
        if (val === null || val === undefined) return '';
        const str = String(val);
        return str.includes(',') || str.includes('"') || str.includes('\n')
          ? `"${str.replace(/"/g, '""')}"`
          : str;
      }).join(',')
    );
    const csv = [header, ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'query-results.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  // Empty state
  if (!hasResult && !error && !isExecuting) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        Run a query to see results
      </div>
    );
  }

  // Loading
  if (isExecuting) {
    return (
      <div className="flex items-center justify-center h-full gap-2 text-gray-500 text-sm">
        <div className="animate-spin rounded-full h-4 w-4 border-2 border-indigo-600 border-t-transparent" />
        Executing query...
      </div>
    );
  }

  // Error
  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-4">
        <p className="text-sm font-medium text-red-800">Error</p>
        <p className="text-sm text-red-600 mt-1">{error}</p>
      </div>
    );
  }

  if (!hasResult) return null;

  // Zero rows — valid result, not an error
  if (rowCount === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500 text-sm">
        Query returned no records
      </div>
    );
  }

  const tabs: { id: ResultTab; label: string; icon: typeof Table }[] = [
    { id: 'auto', label: 'Auto', icon: FileText },
    { id: 'table', label: 'Table', icon: Table },
    { id: 'chart', label: 'Chart', icon: BarChart3 },
  ];

  const renderContent = () => {
    switch (activeTab) {
      case 'table':
        return <ResultsTable data={result.rows} />;
      case 'chart':
        return (
          <ChartRenderer
            data={result.rows}
            columnLabels={presentation?.columnLabels}
          />
        );
      case 'auto':
      default:
        return <ResultRenderer result={result} />;
    }
  };

  return (
    <div className="space-y-3">
      {/* Tab bar + row count + export */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1 rounded-lg border border-gray-200 bg-gray-50 p-0.5">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                  activeTab === tab.id
                    ? 'bg-white text-indigo-700 shadow-sm border border-indigo-200'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                <Icon size={12} />
                {tab.label}
              </button>
            );
          })}
        </div>

        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-400">
            {rowCount.toLocaleString()} row{rowCount !== 1 ? 's' : ''}
          </span>
          {hasPresentation && (
            <span className="inline-flex items-center px-2 py-0.5 text-[10px] font-medium text-indigo-600 bg-indigo-50 border border-indigo-200 rounded-full">
              AI summary available
            </span>
          )}
          <button
            type="button"
            onClick={handleExportCsv}
            className="inline-flex items-center gap-1 px-2 py-1 text-xs text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded transition-colors"
            title="Export as CSV"
          >
            <Download size={12} />
            CSV
          </button>
        </div>
      </div>

      {/* Content */}
      <div>{renderContent()}</div>
    </div>
  );
};
