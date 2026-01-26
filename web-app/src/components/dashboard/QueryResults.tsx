import { ResultsTable } from './ResultsTable';

interface QueryResultsProps {
  results: Record<string, unknown>[] | null;
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
      {(!hasError && results === null && !isCurrentlyExecuting ? (
        <div className="text-center py-12">
          <p className="text-gray-500 text-lg">
            No results yet. Execute a SQL query to see results here.
          </p>
        </div>
      ) : null) as React.JSX.Element | null}

      {/* Loading State */}
      {isExecuting && (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600 mb-4"></div>
          <p className="text-gray-600">Executing query...</p>
        </div>
      )}

      {/* Results Display */}
      {results && !error && !isExecuting && (
        <div className="overflow-x-auto">
          {typeof results === 'string' ? (
            <div className="bg-gray-50 p-4 rounded-lg">
              <pre className="font-mono text-sm text-gray-800 whitespace-pre-wrap break-words">
                {results}
              </pre>
            </div>
          ) : Array.isArray(results) && results.length > 0 ? (
            <ResultsTable data={results} />
          ) : (
            <div className="bg-gray-50 p-4 rounded-lg">
              <pre className="font-mono text-sm text-gray-800 whitespace-pre-wrap break-words">
                {JSON.stringify(results, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};