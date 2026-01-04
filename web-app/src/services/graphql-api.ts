import { SQL_GRAPHQL_BASE_URL } from '../configs/url-config';
import apiClient from './api-client';

interface GraphQLRequest {
  query: string;
  variables?: Record<string, any>;
}

interface GraphQLResponse {
  data?: any;
  errors?: Array<{ message: string }>;
}

export const graphqlApi = {
  executeSqlQuery: async (sqlStatement: string): Promise<GraphQLResponse> => {
    const graphql_query = `
      query GetSqlData($sql: String!) {
        executeSqlStatement(request: { sqlStatement: $sql })
      }
    `;

    const payload: GraphQLRequest = {
      query: graphql_query,
      variables: { sql: sqlStatement },
    };

    try {
      const response = await apiClient.post<GraphQLResponse>(
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

      return response.data;
    } catch (error: any) {
      const message =
        error.response?.data?.errors?.[0]?.message ||
        error.message ||
        'Failed to execute SQL query';
      throw new Error(message);
    }
  },
};
