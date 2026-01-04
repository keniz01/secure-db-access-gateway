import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { dashboardApi } from '../services/dashboard-api';
import { graphqlApi } from '../services/graphql-api';
import useAuth from '../hooks/use-auth';

export const Dashboard = () => {
  const { user, logout } = useAuth();
  const [sqlQuery, setSqlQuery] = useState('');
  const [queryResults, setQueryResults] = useState<any>(null);
  const [isExecuting, setIsExecuting] = useState(false);
  const [queryError, setQueryError] = useState<string | null>(null);

  const { data: dashboardUser, isLoading } = useQuery({
    queryKey: ['dashboard'],
    queryFn: dashboardApi.fetchDashboard,
    retry: 1,
    staleTime: 1000 * 60 * 5,
  });

  const handleExecuteQuery = async () => {
    if (!sqlQuery.trim()) {
      setQueryError('Please enter a SQL query');
      return;
    }

    setIsExecuting(true);
    setQueryError(null);
    setQueryResults(null);

    try {
      const response = await graphqlApi.executeSqlQuery(sqlQuery);
      if (response.data?.executeSqlStatement) {
        setQueryResults(response.data.executeSqlStatement);
      } else if (response.errors) {
        setQueryError(response.errors[0]?.message || 'Query execution failed');
      } else {
        setQueryError('No data returned from query');
      }
    } catch (error: any) {
      setQueryError(error.message || 'Failed to execute query');
    } finally {
      setIsExecuting(false);
    }
  };

  const handleClearResults = () => {
    setSqlQuery('');
    setQueryResults(null);
    setQueryError(null);
  };

  if (isLoading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 flex items-center justify-center">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-16 w-16 border-b-4 border-indigo-600 mb-4"></div>
          <p className="text-gray-600">Loading dashboard...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100">
      {/* Header */}
      <header className="bg-white shadow-md">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <h1 className="text-2xl font-bold text-gray-800">SQL Query Dashboard</h1>
          <button
            onClick={logout}
            className="px-6 py-2 bg-red-600 hover:bg-red-700 text-white font-semibold rounded-lg transition-colors duration-200 shadow-md hover:shadow-lg"
          >
            Logout
          </button>
        </div>
      </header>

      {/* Main Content */}
      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* User Information Card - Top */}
        <div className="bg-white rounded-2xl shadow-lg p-8 mb-8">
          <div className="flex items-center space-x-4">
            <div className="w-16 h-16 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-full flex items-center justify-center flex-shrink-0">
              <span className="text-2xl font-bold text-white">
                {user?.name?.charAt(0)?.toUpperCase() || 'U'}
              </span>
            </div>
            <div>
              <h2 className="text-3xl font-bold text-gray-800">Welcome, {user?.name}!</h2>
              <p className="text-gray-600">{user?.email}</p>
              {dashboardUser?.message && (
                <p className="text-green-600 text-sm mt-1">{dashboardUser?.message?.replace(/"/g, '')}</p>
              )}
            </div>
          </div>
        </div>

        {/* Search Box - Middle */}
        <div className="bg-white rounded-2xl shadow-lg p-8 mb-8">
          <h3 className="text-xl font-bold text-gray-800 mb-6">Execute SQL Query</h3>
          <div className="space-y-4">
            <textarea
              value={sqlQuery}
              onChange={(e) => setSqlQuery(e.target.value)}
              placeholder="Enter your SQL query here... (e.g., SELECT * FROM table WHERE condition)"
              className="w-full h-32 p-4 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent font-mono text-sm"
            />
            <div className="flex space-x-4">
              <button
                onClick={handleExecuteQuery}
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
                onClick={handleClearResults}
                className="px-8 py-3 bg-gray-400 hover:bg-gray-500 text-white font-semibold rounded-lg transition-colors duration-200 shadow-md hover:shadow-lg"
              >
                Clear
              </button>
            </div>
          </div>
        </div>

        {/* Query Results - Bottom */}
        <div className="bg-white rounded-2xl shadow-lg p-8">
          <h3 className="text-xl font-bold text-gray-800 mb-6">Query Results</h3>

          {/* Error Display */}
          {queryError && (
            <div className="bg-red-50 border-l-4 border-red-500 p-6 rounded-lg mb-6">
              <p className="text-red-800 font-semibold">Error</p>
              <p className="text-red-700 mt-2">{queryError}</p>
            </div>
          )}

          {/* No Results */}
          {!queryError && !queryResults && !isExecuting && (
            <div className="text-center py-12">
              <p className="text-gray-500 text-lg">
                No results yet. Execute a SQL query to see results here.
              </p>
            </div>
          )}

          {/* Results Display */}
          {queryResults && !queryError && (
            <div className="overflow-x-auto">
              {typeof queryResults === 'string' ? (
                <div className="bg-gray-50 p-4 rounded-lg">
                  <pre className="font-mono text-sm text-gray-800 whitespace-pre-wrap break-words">
                    {queryResults}
                  </pre>
                </div>
              ) : Array.isArray(queryResults) && queryResults.length > 0 ? (
                <>
                  <div className="mb-4 text-gray-600 text-sm">
                    <p>{queryResults.length} row(s) returned</p>
                  </div>
                  <table className="w-full border-collapse">
                    <thead>
                      <tr className="bg-gray-100 border-b-2 border-gray-300">
                        {Object.keys(queryResults[0]).map((key) => (
                          <th
                            key={key}
                            className="px-4 py-3 text-left text-sm font-semibold text-gray-700"
                          >
                            {key}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {queryResults.map((row: any, idx: number) => (
                        <tr key={idx} className="border-b border-gray-200 hover:bg-gray-50">
                          {Object.values(row).map((val: any, valIdx: number) => (
                            <td
                              key={valIdx}
                              className="px-4 py-3 text-sm text-gray-700 font-mono"
                            >
                              {val !== null && val !== undefined ? String(val) : 'NULL'}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              ) : (
                <div className="bg-gray-50 p-4 rounded-lg">
                  <pre className="font-mono text-sm text-gray-800 whitespace-pre-wrap break-words">
                    {JSON.stringify(queryResults, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};