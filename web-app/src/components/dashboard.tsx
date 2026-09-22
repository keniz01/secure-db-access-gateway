import { useState } from 'react';
import { graphqlApi } from '../services/graphql-api';
import { textToSqlApi } from '../services/text-to-sql-api';
import useAuth from '../hooks/use-auth';
import type { QueryResult } from '../models/query-result';
import { AppLayout, SecurityBanner } from './layout/index';
import { SchemaSidebar } from './schema/index';
import { QueryModeSwitcher, SqlEditor, AskEditor, ExecutionFeedback } from './workspace/index';
import type { QueryMode } from './workspace/index';
import { ResultsPanel } from './results/index';
import { useQuery } from '@tanstack/react-query';

type ExecutionStatus = 'idle' | 'validating' | 'executing' | 'success' | 'error';

const POLICY_CHECKS = [
  { label: 'SQL validated', passed: true },
  { label: 'Read-only policy', passed: true },
  { label: 'No destructive operations', passed: true },
  { label: 'Row limit applied', passed: true },
];

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
  const [queryResults, setQueryResults] = useState<QueryResult | null>(null);
  const [queryError, setQueryError] = useState<string | null>(null);
  const [executionStatus, setExecutionStatus] = useState<ExecutionStatus>('idle');

  const {
    data: schemaTables = [],
    isLoading: isSchemaLoading,
    error: schemaError,
  } = useQuery({
    queryKey: ['schema-browser'],
    queryFn: () => graphqlApi.fetchSchema(),
    retry: 1,
    staleTime: 1000 * 60 * 5,
  });

  const handleInsertIdentifier = (identifier: string) => {
    if (queryMode === 'sql') {
      setSqlQuery((prev) => (prev ? `${prev} ${identifier}` : identifier));
    }
  };

  const handleExecuteSql = async (sql: string) => {
    if (!sql.trim()) {
      setQueryError('Please enter a SQL query');
      return;
    }

    setExecutionStatus('validating');
    setQueryError(null);
    setQueryResults(null);

    // Brief validation phase for UX feedback
    await new Promise((r) => setTimeout(r, 300));

    setExecutionStatus('executing');

    try {
      const result = await graphqlApi.executeSqlQuery(sql);
      setQueryResults(result);
      setExecutionStatus('success');
    } catch (error) {
      setQueryError((error as Error).message || 'Failed to execute query');
      setExecutionStatus('error');
    }
  };

  const handleExecuteDirectSql = () => handleExecuteSql(sqlQuery);

  const handleClear = () => {
    if (queryMode === 'sql') {
      setSqlQuery('');
    } else {
      setNaturalLanguageQuery('');
      setGeneratedSql(null);
    }
    setQueryResults(null);
    setQueryError(null);
    setExecutionStatus('idle');
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

  const handleExecuteNaturalLanguage = async () => {
    if (!generatedSql) {
      setQueryError('Please generate SQL first');
      return;
    }
    handleExecuteSql(generatedSql);
  };

  return (
    <AppLayout
      onLogout={logout}
      securityBanner={
        <SecurityBanner
          databaseId="default"
          isReadOnly={true}
          role={user?.role}
          rowLimit={5000}
        />
      }
      schemaSidebar={
        <SchemaSidebar
          tables={schemaTables}
          isLoading={isSchemaLoading}
          error={schemaError ? (schemaError as Error).message : null}
          onInsertIdentifier={handleInsertIdentifier}
        />
      }
      queryArea={
        <div className="space-y-4">

          {/* Mode switcher */}
          <div className="flex items-center justify-between">
            <QueryModeSwitcher mode={queryMode} onModeChange={setQueryMode} />
          </div>

          {/* Editor */}
          {queryMode === 'sql' ? (
            <SqlEditor
              sql={sqlQuery}
              onSqlChange={setSqlQuery}
              onExecute={handleExecuteDirectSql}
              onClear={handleClear}
              isExecuting={executionStatus === 'executing' || executionStatus === 'validating'}
              schemaTables={schemaTables}
            />
          ) : (
            <AskEditor
              question={naturalLanguageQuery}
              generatedSql={generatedSql}
              onQuestionChange={setNaturalLanguageQuery}
              onGeneratedSqlChange={setGeneratedSql}
              onGenerateSql={handleGenerateSql}
              onExecute={handleExecuteNaturalLanguage}
              onClear={handleClear}
              isGenerating={isGenerating}
              isExecuting={executionStatus === 'executing' || executionStatus === 'validating'}
              schemaTables={schemaTables}
            />
          )}

          {/* Execution feedback */}
          <ExecutionFeedback
            status={executionStatus}
            checks={POLICY_CHECKS}
            databaseId="default"
            errorMessage={queryError ?? undefined}
          />
        </div>
      }
      resultsArea={
        <ResultsPanel
          result={queryResults}
          error={queryError}
          isExecuting={executionStatus === 'executing' || executionStatus === 'validating'}
        />
      }
    />
  );
};
