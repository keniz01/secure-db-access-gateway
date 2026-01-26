interface QueryInputProps {
  sqlQuery: string;
  onQueryChange: (query: string) => void;
  onExecute: () => void;
  onClear: () => void;
  isExecuting: boolean;
}

export const QueryInput = ({
  sqlQuery,
  onQueryChange,
  onExecute,
  onClear,
  isExecuting
}: QueryInputProps) => {
  return (
    <div className="bg-white rounded-2xl shadow-lg p-8 mb-8">
      <h3 className="text-xl font-bold text-gray-800 mb-6">Execute SQL Query</h3>
      <div className="space-y-4">
        <textarea
          value={sqlQuery}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder="Enter your SQL query here... (e.g., SELECT * FROM table WHERE condition)"
          className="w-full h-32 p-4 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent font-mono text-sm"
        />
        <div className="flex space-x-4">
          <button
            onClick={onExecute}
            disabled={isExecuting || !sqlQuery.trim()}
            className="px-8 py-3 bg-indigo-600 hover:bg-indigo-700 disabled:bg-gray-400 text-white font-semibold rounded-lg transition-colors duration-200 shadow-md hover:shadow-lg"
          >
            {isExecuting ? (
              <>
                <span className="inline-block animate-spin mr-2">⚙️</span>
                Executing...
              </>
            ) : (
              'Execute Query'
            )}
          </button>
          <button
            onClick={onClear}
            className="px-8 py-3 bg-gray-400 hover:bg-gray-500 text-white font-semibold rounded-lg transition-colors duration-200 shadow-md hover:shadow-lg"
          >
            Clear
          </button>
        </div>
      </div>
    </div>
  );
};