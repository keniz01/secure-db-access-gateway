import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { dashboardApi } from '../services/dashboard-api';
import { graphqlApi } from '../services/graphql-api';
import useAuth from '../hooks/use-auth';
import {
  DashboardHeader,
  UserInfoCard,
  QueryInput,
  QueryResults
} from './dashboard/index';

interface GraphQLResponse {
  data?: {
    executeSqlStatement?: unknown;
  };
  errors?: Array<{ message: string }>;
}

export const Dashboard = () => {
  const { user, logout } = useAuth();
  const [sqlQuery, setSqlQuery] = useState('');
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
      <DashboardHeader onLogout={logout} />

      <div className="max-w-7xl mx-auto px-6 py-8">
        <UserInfoCard
          user={user}
          dashboardMessage={dashboardUser?.message}
        />

        <QueryInput
          sqlQuery={sqlQuery}
          onQueryChange={setSqlQuery}
          onExecute={handleExecuteQuery}
          onClear={handleClearResults}
          isExecuting={isExecuting}
        />

        <QueryResults
          results={queryResults}
          error={queryError}
          isExecuting={isExecuting}
        />
      </div>
    </div>
  );
};