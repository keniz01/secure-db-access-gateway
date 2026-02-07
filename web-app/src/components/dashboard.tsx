import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { dashboardApi } from '../services/dashboard-api';
import { graphqlApi } from '../services/graphql-api';
import { textToSqlApi } from '../services/text-to-sql-api';
import useAuth from '../hooks/use-auth';
import {
  DashboardHeader,
  UserInfoCard,
  QueryInput,
  NaturalLanguageInput,
  QueryResults
} from './dashboard/index';

interface GraphQLResponse {
  data?: {
    executeSqlStatement?: unknown;
  };
  errors?: Array<{ message: string }>;
}

type QueryMode = 'sql' | 'natural';

export const Dashboard = () => {
  const { user, logout } = useAuth();
  const [queryMode, setQueryMode] = useState<QueryMode>('sql');
  
  // SQL Query state
  const [sqlQuery, setSqlQuery] = useState('');
  
  // Natural Language Query state
  const [naturalLanguageQuery, setNaturalLanguageQuery] = useState('');
  const [generatedSql, setGeneratedSql] = useState<string | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  
  // Shared state
  const [queryResults, setQueryResults] = useState<Record<string, unknown>[] | null>(null);
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
      const response = await graphqlApi.executeSqlQuery(sqlQuery) as GraphQLResponse;
      if (response.data?.executeSqlStatement) {
        setQueryResults(response.data.executeSqlStatement as Record<string, unknown>[]);
      } else if (response.errors) {
        setQueryError(response.errors[0]?.message || 'Query execution failed');
      } else {
        setQueryError('No data returned from query');
      }
    } catch (error) {
      setQueryError((error as Error).message || 'Failed to execute query');
    } finally {
      setIsExecuting(false);
    }
  };

  const handleClearResults = () => {
    if (queryMode === 'sql') {
      setSqlQuery('');
    } else {
      setNaturalLanguageQuery('');
      setGeneratedSql(null);
    }
    setQueryResults(null);
    setQueryError(null);
  };

  const handleGenerateSql = async () => {
    if (!naturalLanguageQuery.trim()) {
      setQueryError('Please enter a natural language query');
      return;
    }

    setIsGenerating(true);
    setQueryError(null);
    setGeneratedSql(null);
    setQueryResults(null);

    try {
      const response = await textToSqlApi.generateSql(naturalLanguageQuery, false);
      if (response.error) {
        setQueryError(response.error);
      } else if (response.sql) {
        setGeneratedSql(response.sql);
      } else {
        setQueryError('Failed to generate SQL');
      }
    } catch (error) {
      setQueryError((error as Error).message || 'Failed to generate SQL');
    } finally {
      setIsGenerating(false);
    }
  };

  const handleSqlChange = (sql: string) => {
    setGeneratedSql(sql);
  };

  const handleExecuteNaturalLanguage = async () => {
    if (!generatedSql) {
      setQueryError('Please generate SQL first');
      return;
    }

    setIsExecuting(true);
    setQueryError(null);
    setQueryResults(null);

    try {
      // Execute the generated SQL using the existing GraphQL API
      const response = await graphqlApi.executeSqlQuery(generatedSql) as GraphQLResponse;
      if (response.data?.executeSqlStatement) {
        setQueryResults(response.data.executeSqlStatement as Record<string, unknown>[]);
      } else if (response.errors) {
        setQueryError(response.errors[0]?.message || 'Query execution failed');
      } else {
        setQueryError('No data returned from query');
      }
    } catch (error) {
      setQueryError((error as Error).message || 'Failed to execute query');
    } finally {
      setIsExecuting(false);
    }
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
      <DashboardHeader onLogout={logout} />

      <div className="max-w-7xl mx-auto px-6 py-8">
        <UserInfoCard
          user={user}
          dashboardMessage={dashboardUser?.message}
        />

        {/* Tab Navigation */}
        <div className="bg-white rounded-2xl shadow-lg p-2 mb-8">
          <div className="flex space-x-2">
            <button
              onClick={() => {
                setQueryMode('sql');
                handleClearResults();
              }}
              className={`flex-1 px-6 py-3 rounded-lg font-semibold transition-colors duration-200 ${
                queryMode === 'sql'
                  ? 'bg-indigo-600 text-white shadow-md'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
            >
              SQL Query
            </button>
            <button
              onClick={() => {
                setQueryMode('natural');
                handleClearResults();
              }}
              className={`flex-1 px-6 py-3 rounded-lg font-semibold transition-colors duration-200 ${
                queryMode === 'natural'
                  ? 'bg-indigo-600 text-white shadow-md'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
            >
              Natural Language Query
            </button>
          </div>
        </div>

        {/* Query Input based on mode */}
        {queryMode === 'sql' ? (
          <QueryInput
            sqlQuery={sqlQuery}
            onQueryChange={setSqlQuery}
            onExecute={handleExecuteQuery}
            onClear={handleClearResults}
            isExecuting={isExecuting}
          />
        ) : (
          <NaturalLanguageInput
            query={naturalLanguageQuery}
            generatedSql={generatedSql}
            onQueryChange={setNaturalLanguageQuery}
            onSqlChange={handleSqlChange}
            onGenerateSql={handleGenerateSql}
            onExecute={handleExecuteNaturalLanguage}
            onClear={handleClearResults}
            isGenerating={isGenerating}
            isExecuting={isExecuting}
          />
        )}

        <QueryResults
          results={queryResults}
          error={queryError}
          isExecuting={isExecuting || isGenerating}
        />
      </div>
    </div>
  );
};