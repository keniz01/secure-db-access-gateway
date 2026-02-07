interface NaturalLanguageInputProps {
  query: string;
  generatedSql: string | null;
  onQueryChange: (query: string) => void;
  onSqlChange?: (sql: string) => void;
  onGenerateSql: () => void;
  onExecute: () => void;
  onClear: () => void;
  isGenerating: boolean;
  isExecuting: boolean;
}

export const NaturalLanguageInput = ({
  query,
  generatedSql,
  onQueryChange,
  onSqlChange,
  onGenerateSql,
  onExecute,
  onClear,
  isGenerating,
  isExecuting
}: NaturalLanguageInputProps) => {
  return (
    <div className="bg-white rounded-2xl shadow-lg p-8 mb-8">
      <h3 className="text-xl font-bold text-gray-800 mb-6">Natural Language Query</h3>
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Ask a question in plain English
          </label>
          <textarea
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder="e.g., Show me all albums released in 2000"
            className="w-full h-32 p-4 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent text-sm"
          />
        </div>
        
        {generatedSql && (
          <div className="mt-4">
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Generated SQL (you can edit before executing)
            </label>
            <textarea
              value={generatedSql}
              onChange={(e) => onSqlChange?.(e.target.value)}
              className="w-full h-24 p-4 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent font-mono text-xs"
            />
          </div>
        )}

        <div className="flex space-x-4">
          <button
            onClick={onGenerateSql}
            disabled={isGenerating || !query.trim()}
            className="px-8 py-3 bg-indigo-600 hover:bg-indigo-700 disabled:bg-gray-400 text-white font-semibold rounded-lg transition-colors duration-200 shadow-md hover:shadow-lg"
          >
            {isGenerating ? (
              <>
                <span className="inline-block animate-spin mr-2">⚙️</span>
                Generating SQL...
              </>
            ) : (
              'Generate SQL'
            )}
          </button>
          
          {generatedSql && (
            <button
              onClick={onExecute}
              disabled={isExecuting || !generatedSql.trim()}
              className="px-8 py-3 bg-green-600 hover:bg-green-700 disabled:bg-gray-400 text-white font-semibold rounded-lg transition-colors duration-200 shadow-md hover:shadow-lg"
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
          )}
          
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

