import type { QueryResult } from '../../models/query-result';
import { ResultRenderer } from './ResultRenderer';

interface QueryResultsProps {
  results: QueryResult | null;
  error: string | null;
  isExecuting: boolean;
}

export const QueryResults = ({ results, error, isExecuting }: QueryResultsProps): React.JSX.Element => {
  const hasError = Boolean(error);
  const isCurrentlyExecuting = Boolean(isExecuting);

  return (
    <div className="bg-white rounded-2xl shadow-lg p-8">
      <h3 className="text-xl font-bold text-gray-800 mb-6">Query Results</h3>

      {/* Error Display */}
      {error && (
        <div className="bg-red-50 border-l-4 border-red-500 p-6 rounded-lg mb-6">
          <p className="text-red-800 font-semibold">Error</p>
          <p className="text-red-700 mt-2">{error}</p>
        </div>
      )}

      {/* No Results */}
      {(!hasError && results === null && !isCurrentlyExecuting) && (
        <div className="text-center py-12">
          <p className="text-gray-500 text-lg">
            No results yet. Execute a SQL query to see results here.
          </p>
        </div>
      )}

      {/* Loading State */}
      {isExecuting && (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600 mb-4"></div>
          <p className="text-gray-600">Executing query...</p>
        </div>
      )}

      {/* Results Display */}
      {results && !error && !isExecuting && (
        <ResultRenderer result={results} />
      )}
    </div>
  );
};