import { DEFAULT_DATABASE_ID, SQL_GRAPHQL_BASE_URL } from '../configs/url-config';
import type { PresentationDecision, PresentationFormat, QueryResult } from '../models/query-result';
import apiClient from './api-client';

interface GraphQLRequest {
  query: string;
  variables?: Record<string, unknown>;
}

interface GraphQLResponse {
  data?: unknown;
  errors?: Array<{ message: string }>;
}

interface SchemaColumn {
  name: string;
  type: string;
  nullable: boolean;
  isPrimary: boolean;
}

interface SchemaForeignKey {
  column: string;
  foreignTable: string;
  foreignColumn: string;
}

interface SchemaTable {
  name: string;
  schemaName: string;
  columns: SchemaColumn[];
  foreignKeys: SchemaForeignKey[];
}

interface IntrospectSchemaResponse {
  data?: {
    introspectSchema?: {
      tables?: SchemaTable[];
    };
  };
  errors?: Array<{ message: string }>;
}

const SUPPORTED_FORMATS: PresentationFormat[] = ['paragraph', 'list', 'chart', 'table'];

const normalizePresentation = (
  presentation: PresentationDecision | null | undefined
): PresentationDecision | null => {
  if (!presentation) {
    return null;
  }
  const lower = presentation.format?.toLowerCase?.() ?? 'table';
  const format = SUPPORTED_FORMATS.includes(lower as PresentationFormat)
    ? (lower as PresentationFormat)
    : 'table';
  return { ...presentation, format };
};

export const graphqlApi = {
  fetchSchema: async (databaseId: string = DEFAULT_DATABASE_ID): Promise<SchemaTable[]> => {
    const graphql_query = `
      query IntrospectSchema($databaseId: String!) {
        introspectSchema(databaseId: $databaseId) {
          tables {
            name
            schemaName
            columns {
              name
              type
              nullable
              isPrimary
            }
            foreignKeys {
              column
              foreignTable
              foreignColumn
            }
          }
        }
      }
    `;

    const payload: GraphQLRequest = {
      query: graphql_query,
      variables: { databaseId },
    };

    try {
      const response = await apiClient.post<IntrospectSchemaResponse>(
        SQL_GRAPHQL_BASE_URL,
        payload,
        {
          headers: {
            'Content-Type': 'application/json',
          },
        }
      );

      if (response.data.errors) {
        throw new Error(response.data.errors[0]?.message || 'GraphQL error');
      }

      return response.data.data?.introspectSchema?.tables || [];
    } catch (error) {
      const message =
        (error as Error & { response?: { data?: { errors?: Array<{ message: string }> } } }).response?.data?.errors?.[0]?.message ||
        (error as Error).message ||
        'Failed to load database schema';
      throw new Error(message);
    }
  },

  executeSqlQuery: async (
    sqlStatement: string,
    databaseId: string = DEFAULT_DATABASE_ID,
    question?: string
  ): Promise<QueryResult> => {
    const graphql_query = `
      query GetSqlData($sql: String!, $databaseId: String!, $question: String = "") {
        executeSqlStatementWithPresentation(
          request: { sqlStatement: $sql, databaseId: $databaseId, question: $question }
        ) {
          rows
          presentation {
            format
            content
            reason
            columnLabels
          }
        }
      }
    `;

    const payload: GraphQLRequest = {
      query: graphql_query,
      variables: { sql: sqlStatement, databaseId, question },
    };

    try {
      const response = await apiClient.post<
        GraphQLResponse & { data?: { executeSqlStatementWithPresentation?: QueryResult } }
      >(
        SQL_GRAPHQL_BASE_URL,
        payload,
        {
          headers: {
            'Content-Type': 'application/json',
          },
        }
      );

      if (response.data.errors) {
        throw new Error(response.data.errors[0]?.message || 'GraphQL error');
      }

      const result = response.data.data?.executeSqlStatementWithPresentation;
      if (!result) {
        throw new Error('No data returned from query');
      }

      return {
        rows: Array.isArray(result.rows) ? result.rows : [],
        presentation: normalizePresentation(result.presentation),
      };
    } catch (error) {
      const message =
        (error as Error & { response?: { data?: { errors?: Array<{ message: string }> } } }).response?.data?.errors?.[0]?.message ||
        (error as Error).message ||
        'Failed to execute SQL query';
      throw new Error(message);
    }
  },
};
